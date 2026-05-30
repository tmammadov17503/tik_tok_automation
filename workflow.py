from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SEGMENT_PATTERN = re.compile(r"^\s*(?P<start>[^-]+)\s*-\s*(?P<end>[^-]+)\s*$")
DURATION_PATTERN = re.compile(r"Duration:\s*(\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)")
VIDEO_STREAM_PATTERN = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
MEAN_VOLUME_PATTERN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
MAX_VOLUME_PATTERN = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
NON_SPEECH_PATTERN = re.compile(
    r"(?iu)(?:\[(?:музыка|music|аплодисменты|applause|смех|laughter|шум|noise|вздохи?|sighs?|кашель|coughing)\]"
    r"|\((?:музыка|music|аплодисменты|applause|смех|laughter|шум|noise|вздохи?|sighs?|кашель|coughing)\))"
)


def _module_exists(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def ffmpeg_command() -> list[str] | None:
    binary = shutil.which("ffmpeg")
    if binary:
        return [binary]
    if _module_exists("imageio_ffmpeg"):
        import imageio_ffmpeg

        return [imageio_ffmpeg.get_ffmpeg_exe()]
    return None


def yt_dlp_command() -> list[str] | None:
    binary = shutil.which("yt-dlp")
    if binary:
        return [binary]
    if _module_exists("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    return None


def whisper_command() -> list[str] | None:
    binary = shutil.which("whisper")
    if binary:
        return [binary]
    if _module_exists("whisper"):
        return [sys.executable, "-m", "whisper"]
    return None


def openai_transcription_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY")) and _module_exists("openai")


def openai_transcription_model() -> str:
    return os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1").strip() or "whisper-1"


def yt_dlp_js_args() -> list[str]:
    if shutil.which("node"):
        args = ["--js-runtimes", "node"]
        if not _module_exists("yt_dlp_ejs"):
            args.extend(["--remote-components", "ejs:github"])
        return args
    if shutil.which("deno"):
        return ["--js-runtimes", "deno", "--remote-components", "ejs:npm"]
    return []


def yt_dlp_video_extractor_args() -> str:
    return "youtube:player_client=web_safari,web"


def yt_dlp_video_format() -> str:
    return (
        "best[protocol=m3u8_native][height<=1080]/"
        "best[protocol=m3u8][height<=1080]/"
        "bestvideo[height<=1080]*+bestaudio/"
        "best[height<=1080]/"
        "best"
    )


def requested_output_fps(request: dict[str, Any]) -> int | None:
    raw = str(request.get("frame_rate") or "source").strip().lower()
    if raw in {"50", "60"}:
        return int(raw)
    return None


def detect_tools() -> dict[str, bool]:
    return {
        "ffmpeg": ffmpeg_command() is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "yt_dlp": yt_dlp_command() is not None,
        "whisper": whisper_command() is not None,
        "openai_transcription": openai_transcription_available(),
    }


def slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return clean or "video-project"


def parse_seconds(raw: str) -> float:
    parts = [int(part) for part in raw.strip().split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        hours = 0
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Unsupported timecode: {raw}")
    return float(hours * 3600 + minutes * 60 + seconds)


def parse_clock_seconds(raw: str) -> float:
    clean = raw.strip().replace(",", ".")
    parts = clean.split(":")
    if len(parts) != 3:
        raise ValueError(f"Unsupported clock format: {raw}")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_clock(value: float) -> str:
    total = max(0, int(round(value)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def seconds_to_ffmpeg_time(value: float) -> str:
    bounded = max(0.0, value)
    hours = int(bounded // 3600)
    minutes = int((bounded % 3600) // 60)
    seconds = bounded - (hours * 3600) - (minutes * 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def seconds_to_srt_time(value: float) -> str:
    bounded = max(0.0, value)
    total_ms = int(round(bounded * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    seconds = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def run_command(
    command: list[str],
    cwd: Path | None = None,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_match(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    return file_sha256(left) == file_sha256(right)


def ffmpeg_subtitles_filter(path: Path) -> str:
    escaped = str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    style = (
        "Fontname=Arial Black,Fontsize=12,Bold=1,Outline=2.1,Shadow=0.8,"
        "Alignment=2,MarginL=72,MarginR=72,MarginV=108,BorderStyle=1,Spacing=0.08,WrapStyle=2,"
        "PrimaryColour=&H00FFFFFF&,OutlineColour=&H00101010&,BackColour=&H00000000&"
    )
    return f"subtitles='{escaped}':force_style='{style}'"


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def clean_caption_text(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\{[^}]+\}", "", clean)
    clean = clean.replace("&nbsp;", " ")
    clean = NON_SPEECH_PATTERN.sub(" ", clean)
    clean = re.sub(
        r"(?iu)\b(?:телефонный звонок|звонок телефона|phone ringing|door opens?|door closes?|стук в дверь|шум|смех|аплодисменты)\b",
        " ",
        clean,
    )
    clean = re.sub(r"\s+", " ", clean)
    clean = clean.strip(" -\u2013\u2014")
    clean = dedupe_repeated_phrases(clean)
    return clean.strip()


def dedupe_repeated_phrases(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return text

    deduped: list[str] = []
    index = 0
    while index < len(tokens):
        matched = False
        max_size = min(4, (len(tokens) - index) // 2)
        for size in range(max_size, 0, -1):
            left = [token.lower() for token in tokens[index : index + size]]
            right = [token.lower() for token in tokens[index + size : index + (2 * size)]]
            if left == right:
                deduped.extend(tokens[index : index + size])
                index += size * 2
                matched = True
                break
        if not matched:
            deduped.append(tokens[index])
            index += 1
    return " ".join(deduped)


def caption_key(text: str) -> str:
    return " ".join(re.findall(r"[0-9A-Za-z\u0400-\u04FF]+", text.lower()))


def has_caption_letters(text: str) -> bool:
    return bool(re.search(r"[A-Za-z\u0400-\u04FF]", text))


def caption_word_count(text: str) -> int:
    words = re.findall(r"[0-9A-Za-z\u0400-\u04FF']+", text)
    return sum(1 for word in words if has_caption_letters(word))


def is_low_quality_caption(text: str) -> bool:
    key = caption_key(text)
    if not key:
        return True

    tokens = key.split()
    if not tokens:
        return True
    if not has_caption_letters(key):
        return True

    numeric_tokens = sum(1 for token in tokens if token.isdigit())
    if len(tokens) >= 3 and numeric_tokens / len(tokens) >= 0.55:
        return True

    if len(tokens) == 1:
        token = tokens[0]
        if len(token) <= 2:
            return True
        if re.fullmatch(r"[a-z]{1,5}", token):
            return True
        common_short_words = {
            "да",
            "нет",
            "ну",
            "ой",
            "эй",
            "ах",
            "эх",
            "ага",
            "иди",
            "стой",
            "тихо",
            "ладно",
            "слушай",
        }
        if len(token) <= 4 and token not in common_short_words:
            return True
    if text.isupper() and len(tokens) <= 4:
        return True
    return False


def caption_relation(current: str, candidate: str) -> str:
    current_key = caption_key(current)
    candidate_key = caption_key(candidate)
    if not current_key or not candidate_key:
        return "different"
    if current_key == candidate_key:
        return "same"
    if current_key in candidate_key:
        return "candidate_contains_current"
    if candidate_key in current_key:
        return "current_contains_candidate"

    current_tokens = set(current_key.split())
    candidate_tokens = set(candidate_key.split())
    overlap = len(current_tokens & candidate_tokens)
    smaller = max(1, min(len(current_tokens), len(candidate_tokens)))
    if overlap / smaller >= 0.75:
        return "high_overlap"
    return "different"


def preferred_caption_text(current: str, candidate: str) -> str:
    relation = caption_relation(current, candidate)
    if relation == "current_contains_candidate":
        return current
    if relation in {"candidate_contains_current", "high_overlap"}:
        return candidate if len(candidate) >= len(current) else current
    return candidate if len(candidate) > len(current) else current


def merge_caption_text(current: str, candidate: str, max_chars: int) -> str | None:
    relation = caption_relation(current, candidate)
    if relation == "same":
        return preferred_caption_text(current, candidate)
    if relation == "candidate_contains_current":
        return candidate if len(candidate) <= max_chars else None
    if relation == "current_contains_candidate":
        return current if len(current) <= max_chars else None

    current_words = current.split()
    candidate_words = candidate.split()
    max_overlap = min(5, len(current_words), len(candidate_words))
    for size in range(max_overlap, 1, -1):
        left = [token.lower() for token in current_words[-size:]]
        right = [token.lower() for token in candidate_words[:size]]
        if left == right:
            merged = " ".join(current_words + candidate_words[size:])
            merged = clean_caption_text(merged)
            return merged if len(merged) <= max_chars else None

    merged = clean_caption_text(f"{current} {candidate}")
    return merged if len(merged) <= max_chars else None


def wrap_caption_text(text: str, max_line_chars: int = 19, max_lines: int = 2) -> str:
    words = text.split()
    if not words:
        return text

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and len(candidate) > max_line_chars and len(lines) < max_lines - 1:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)

    if current:
        lines.append(" ".join(current))

    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [tail]

    return "\n".join(line.strip() for line in lines if line.strip())


def without_proxy_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "GIT_HTTP_PROXY",
        "GIT_HTTPS_PROXY",
    ]:
        env.pop(key, None)
    return env


def runtime_env(root: Path | None = None) -> dict[str, str]:
    env = without_proxy_env()
    ffmpeg = ffmpeg_command()
    if ffmpeg:
        ffmpeg_path = Path(ffmpeg[0]).resolve()
        ffmpeg_dir = ffmpeg_path.parent
        if root is not None and ffmpeg_path.name.lower() != "ffmpeg.exe":
            tool_dir = root / ".tools"
            tool_dir.mkdir(parents=True, exist_ok=True)
            shim_path = tool_dir / "ffmpeg.exe"
            if not shim_path.exists() or shim_path.stat().st_size != ffmpeg_path.stat().st_size:
                shutil.copy2(ffmpeg_path, shim_path)
            ffmpeg_dir = tool_dir
        current_path = env.get("PATH", "")
        ffmpeg_dir_str = str(ffmpeg_dir)
        if ffmpeg_dir_str.lower() not in current_path.lower():
            env["PATH"] = ffmpeg_dir_str if not current_path else f"{ffmpeg_dir_str};{current_path}"
    return env


def yt_dlp_ffmpeg_args(root: Path | None = None) -> list[str]:
    ffmpeg = ffmpeg_command()
    if not ffmpeg:
        return []
    ffmpeg_path = Path(ffmpeg[0]).resolve()
    ffmpeg_dir = ffmpeg_path.parent
    if root is not None and ffmpeg_path.name.lower() != "ffmpeg.exe":
        tool_dir = root / ".tools"
        tool_dir.mkdir(parents=True, exist_ok=True)
        shim_path = tool_dir / "ffmpeg.exe"
        if not shim_path.exists() or shim_path.stat().st_size != ffmpeg_path.stat().st_size:
            shutil.copy2(ffmpeg_path, shim_path)
        ffmpeg_dir = tool_dir
    return ["--ffmpeg-location", str(ffmpeg_dir)]


def yt_dlp_cookie_browser_candidates() -> list[str]:
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming_appdata = Path(os.environ.get("APPDATA", ""))

    candidates = [
        ("edge", local_appdata / "Microsoft" / "Edge" / "User Data"),
        ("chrome", local_appdata / "Google" / "Chrome" / "User Data"),
        ("brave", local_appdata / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("chromium", local_appdata / "Chromium" / "User Data"),
        ("firefox", roaming_appdata / "Mozilla" / "Firefox" / "Profiles"),
    ]
    return [name for name, path in candidates if path.exists()]


def yt_dlp_cookie_file_args(root: Path | None = None) -> list[str]:
    raw_path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    candidates: list[Path] = []
    if raw_path:
        candidates.append(Path(raw_path).expanduser())
    if root is not None:
        candidates.append(root / ".secrets" / "youtube_cookies.txt")

    for path in candidates:
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return ["--cookies", str(path.resolve())]
    return []


def yt_dlp_error_summary(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    combined = "\n".join(part for part in [stderr, stdout] if part).strip()
    if not combined:
        return "yt-dlp failed without an error message."
    return combined.splitlines()[-1]


def is_youtube_bot_error(completed: subprocess.CompletedProcess[str]) -> bool:
    combined = "\n".join(
        part for part in [(completed.stderr or "").lower(), (completed.stdout or "").lower()] if part
    )
    return (
        "sign in to confirm you’re not a bot" in combined
        or "sign in to confirm you're not a bot" in combined
        or "http error 429" in combined
    )


def contains_cyrillic(text: str) -> bool:
    return bool(re.search(r"[\u0400-\u04FF]", text))


def subtitle_languages_for_request(request: dict[str, Any]) -> str:
    language = str(request.get("language") or "auto").strip().lower()
    if language.startswith("ru"):
        return "ru.*,ru,-live_chat"
    if language.startswith("en"):
        return "en.*,en,-live_chat"
    return "en.*,en,ru.*,ru,-live_chat"


def decorate_subtitle_text(text: str, line_index: int, language_hint: str) -> str:
    clean = clean_caption_text(text)
    if not clean:
        return clean

    clean = clean.replace("...", "\u2026")
    clean = wrap_caption_text(clean, max_line_chars=19, max_lines=2)

    emoji = ""
    lower = clean.lower()
    is_russian = language_hint.startswith("ru") or contains_cyrillic(clean)
    if line_index % 7 == 0:
        if "!" in clean:
            emoji = " \U0001F62E"
        elif "?" in clean:
            emoji = " \U0001F914"
        elif any(token in lower for token in ["\u043d\u0435\u0442", "\u0441\u0442\u043e\u0439", "\u0442\u0438\u0445\u043e", "\u0441\u043c\u043e\u0442\u0440\u0438", "\u043f\u043e\u0447\u0435\u043c\u0443"]):
            emoji = " \U0001F633"
        elif any(token in lower for token in ["no", "wait", "look", "why", "stop"]):
            emoji = " \U0001F633"
        elif any(token in lower for token in ["\u043b\u044e\u0431", "\u0441\u0435\u0440\u0434\u0446", "love", "heart"]):
            emoji = " \u2764\ufe0f"
        elif any(token in lower for token in ["\u0434\u0435\u043d\u044c\u0433\u0438", "\u0432\u043b\u0430\u0441\u0442\u044c", "money", "power", "win"]):
            emoji = " \U0001F525"

    if is_russian:
        return clean + emoji
    return clean + emoji


def retime_subtitle_window(start: float, end: float, max_end: float | None = None) -> tuple[float, float]:
    adjusted_start = max(0.0, start - 0.12)
    adjusted_end = max(adjusted_start + 0.42, end + 0.06)
    if max_end is not None:
        adjusted_end = min(max_end, adjusted_end)
        adjusted_end = max(adjusted_start + 0.32, adjusted_end)
    return adjusted_start, adjusted_end


def group_subtitle_entries(
    entries: list["SubtitleEntry"],
    *,
    max_gap: float = 0.10,
    max_chars: int = 42,
    max_duration: float = 2.8,
) -> list["SubtitleEntry"]:
    grouped: list[SubtitleEntry] = []
    current: SubtitleEntry | None = None

    for entry in entries:
        caption = clean_caption_text(entry.text)
        if not caption or is_low_quality_caption(caption):
            continue

        candidate = SubtitleEntry(start=entry.start, end=entry.end, text=caption)
        if current is None:
            current = candidate
            continue

        gap = candidate.start - current.end
        relation = caption_relation(current.text, candidate.text)
        if gap <= 0.35 and relation in {"same", "candidate_contains_current", "current_contains_candidate", "high_overlap"}:
            current = SubtitleEntry(
                start=min(current.start, candidate.start),
                end=max(current.end, candidate.end),
                text=preferred_caption_text(current.text, candidate.text),
            )
            continue

        merged_text = merge_caption_text(current.text, candidate.text, max_chars)
        merged_duration = candidate.end - current.start
        if gap <= max_gap and merged_text and merged_duration <= max_duration:
            current = SubtitleEntry(start=current.start, end=candidate.end, text=merged_text)
            continue

        grouped.append(current)
        current = candidate

    if current is not None:
        grouped.append(current)

    return grouped


@dataclass
class SubtitleEntry:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    score: float | None = None
    strategy: str = "manual"
    reason: str = ""
    excerpt: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_seconds": round(self.start, 2),
            "end_seconds": round(self.end, 2),
            "start": seconds_to_clock(self.start),
            "end": seconds_to_clock(self.end),
            "duration_seconds": round(self.duration, 2),
            "score": None if self.score is None else round(self.score, 4),
            "strategy": self.strategy,
            "reason": self.reason,
            "excerpt": self.excerpt,
        }


@dataclass
class SourceBundle:
    source_path: Path
    subtitle_path: Path | None = None
    info_path: Path | None = None
    title: str | None = None
    duration: float | None = None


@dataclass
class PipelineResult:
    output_dir: Path
    artifacts: list[tuple[str, str]]


class WorkflowPipeline:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.output_root = root / "output"
        self.model_root = root / ".models" / "whisper"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)

    def run(self, job: Any, payload: dict[str, Any]) -> PipelineResult:
        request = self._normalize_request(payload)
        self._validate_request(request)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        project_name = slugify(request["project_name"])
        job_dir = self.output_root / f"{timestamp}-{project_name}-{job.job_id}"
        job_dir.mkdir(parents=True, exist_ok=True)

        job.output_dir = str(job_dir)
        job.log(f"Workspace ready at {job_dir}")

        request_path = job_dir / "request.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        bundle = self._resolve_source(job, request, job_dir)
        job.log(f"Source resolved: {bundle.source_path}")
        self._save_source_cache(job, request, bundle)
        source_width, source_height = self._probe_video_dimensions(job, bundle.source_path)
        if source_width and source_height:
            job.log(f"Source dimensions: {source_width}x{source_height}")
            if source_height < 720:
                job.log("Source stayed low-resolution, so the renderer will use an enhanced full-screen crop.")
        if bundle.subtitle_path:
            job.log(f"Subtitle sidecar detected: {bundle.subtitle_path.name}")

        subtitle_entries = self._load_subtitles(bundle.subtitle_path) if bundle.subtitle_path else []
        if subtitle_entries:
            job.log(f"Loaded {len(subtitle_entries)} subtitle cues for highlight analysis.")

        segments, analysis = self._build_segments(job, request, bundle, subtitle_entries, job_dir)
        segments_path = job_dir / "segments.json"
        segments_path.write_text(
            json.dumps([segment.as_dict() for segment in segments], indent=2),
            encoding="utf-8",
        )

        analysis_path = job_dir / "analysis.json"
        analysis_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")

        clip_outputs = self._render_segments(job, request, bundle.source_path, segments, job_dir)
        final_outputs = self._caption_outputs(
            job,
            request,
            bundle.source_path,
            clip_outputs,
            job_dir,
            subtitle_entries,
        )
        metadata_path = self._build_metadata(
            job,
            request,
            final_outputs,
            segments,
            analysis,
            bundle,
            job_dir,
        )
        self._build_upload_notes(job, request, final_outputs, analysis, bundle, job_dir)
        subtitle_paths = [
            Path(output["subtitle_path"])
            for output in final_outputs
            if output.get("subtitle_path")
        ]
        self._cleanup_current_run_intermediates(
            job,
            job_dir,
            keep_paths=[
                request_path,
                analysis_path,
                segments_path,
                metadata_path,
                job_dir / "NEXT_STEPS.md",
                *[Path(output["path"]) for output in final_outputs],
                *subtitle_paths,
            ],
        )

        artifacts = [
            ("Request", "request.json"),
            ("Highlight Analysis", "analysis.json"),
            ("Segment Plan", "segments.json"),
            ("Metadata", metadata_path.name),
            ("Next Steps", "NEXT_STEPS.md"),
        ]

        for output in final_outputs:
            artifacts.append((output["label"], output["path"].name))

        if not final_outputs:
            manifest = job_dir / "render_manifest.json"
            manifest.write_text(json.dumps(clip_outputs, indent=2), encoding="utf-8")
            artifacts.append(("Render Manifest", manifest.name))

        self._cleanup_previous_outputs(job, job_dir)
        return PipelineResult(output_dir=job_dir, artifacts=artifacts)

    def _cleanup_previous_outputs(self, job: Any, keep_dir: Path) -> None:
        removed_items = 0
        failed_items: list[str] = []

        for path in self.output_root.iterdir():
            if path == keep_dir:
                continue

            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed_items += 1
            except Exception:
                failed_items.append(path.name)

        if removed_items:
            job.log(f"Auto-cleanup removed {removed_items} older output item(s).")
        if failed_items:
            job.log(f"Auto-cleanup skipped: {', '.join(failed_items)}")

    def _save_source_cache(self, job: Any, request: dict[str, Any], bundle: SourceBundle) -> None:
        raw_cache_dir = str(request.get("source_cache_dir") or "").strip()
        if not raw_cache_dir:
            return

        cache_dir = Path(raw_cache_dir).expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        if bundle.source_path.resolve().parent == cache_dir:
            return

        target_source = cache_dir / f"source{bundle.source_path.suffix.lower() or '.mp4'}"
        for old_source in cache_dir.glob("source.*"):
            if old_source.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm", ".m4v"} and old_source != target_source:
                try:
                    old_source.unlink()
                except Exception:
                    pass

        if not files_match(bundle.source_path, target_source):
            shutil.copy2(bundle.source_path, target_source)

        if bundle.subtitle_path and bundle.subtitle_path.exists():
            target_subtitle = cache_dir / f"source{bundle.subtitle_path.suffix.lower()}"
            shutil.copy2(bundle.subtitle_path, target_subtitle)

        if bundle.info_path and bundle.info_path.exists():
            shutil.copy2(bundle.info_path, cache_dir / "source.info.json")

        job.log("Cached source media for future scheduled clips.")

    def _cleanup_current_run_intermediates(
        self,
        job: Any,
        job_dir: Path,
        keep_paths: list[Path],
    ) -> None:
        keep_set = {path.resolve() for path in keep_paths if path.exists()}
        removed_items = 0

        for path in job_dir.iterdir():
            resolved = path.resolve()
            if resolved in keep_set:
                continue

            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed_items += 1
            except Exception:
                continue

        if removed_items:
            job.log(f"Run cleanup removed {removed_items} temporary file(s).")

    def _normalize_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_name": str(payload.get("project_name") or "Video Generator Agent"),
            "topic": str(payload.get("topic") or "").strip(),
            "source_mode": str(payload.get("source_mode") or "remote_url"),
            "source_value": str(payload.get("source_value") or "").strip(),
            "segments": str(payload.get("segments") or "").strip(),
            "clip_duration_sec": int(payload.get("clip_duration_sec") or 30),
            "clips_count": max(1, min(int(payload.get("clips_count") or 2), 8)),
            "selection_offset": max(0, int(payload.get("selection_offset") or 0)),
            "frame_rate": str(payload.get("frame_rate") or "source").strip(),
            "language": str(payload.get("language") or "auto").strip(),
            "whisper_model": str(payload.get("whisper_model") or "small").strip(),
            "add_captions": bool(payload.get("add_captions")),
            "publish_mode": str(payload.get("publish_mode") or "manual"),
            "rights_confirmed": bool(payload.get("rights_confirmed")),
            "source_cache_dir": str(payload.get("source_cache_dir") or "").strip(),
            "source_queue_id": str(payload.get("source_queue_id") or "").strip(),
            "source_original_url": str(payload.get("source_original_url") or "").strip(),
            "hashtags": [
                str(tag).strip()
                for tag in (payload.get("hashtags") or [])
                if str(tag).strip()
            ],
            "caption_hint": str(payload.get("caption_hint") or "").strip(),
        }

    def _validate_request(self, request: dict[str, Any]) -> None:
        if not request["source_value"]:
            raise ValueError("Please provide a source URL or local file path.")
        if not request["rights_confirmed"]:
            raise ValueError("Please confirm that you own the content or have permission to use it.")
        if request["source_mode"] not in {"remote_url", "local_file"}:
            raise ValueError("Unsupported source mode.")
        if str(request.get("frame_rate") or "source").strip().lower() not in {"source", "50", "60"}:
            raise ValueError("Unsupported output FPS. Use source, 50, or 60.")
        if request["publish_mode"] not in {"manual", "tiktok_api"}:
            raise ValueError("Unsupported publish mode.")

    def _run_yt_dlp_command(
        self,
        job: Any,
        command: list[str],
        *,
        allow_browser_cookies: bool,
    ) -> subprocess.CompletedProcess[str]:
        completed = run_command(command, env=runtime_env(self.root), check=False)
        if completed.returncode == 0:
            return completed

        if allow_browser_cookies and is_youtube_bot_error(completed):
            for browser in yt_dlp_cookie_browser_candidates():
                job.log(f"YouTube requested sign-in; retrying with browser cookies from {browser}.")
                retry_command = command[:1] + ["--cookies-from-browser", browser] + command[1:]
                retry = run_command(retry_command, env=runtime_env(self.root), check=False)
                if retry.returncode == 0:
                    return retry

            raise RuntimeError(
                "YouTube blocked the download request. The app retried with local browser cookies but access was still denied."
            )

        raise RuntimeError(yt_dlp_error_summary(completed))

    def _resolve_source(self, job: Any, request: dict[str, Any], job_dir: Path) -> SourceBundle:
        source_mode = request["source_mode"]
        source_value = request["source_value"]

        if source_mode == "local_file":
            source_path = Path(source_value).expanduser().resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"Local source not found: {source_path}")
            subtitle_path = self._find_local_subtitle(source_path, request.get("language"))
            info_path = source_path.with_name("source.info.json")
            info_payload = self._read_info_json(info_path) if info_path.exists() else {}
            duration = self._safe_float(info_payload.get("duration")) or self._probe_duration(job, source_path)
            return SourceBundle(
                source_path=source_path,
                subtitle_path=subtitle_path,
                info_path=info_path if info_path.exists() else None,
                title=str(info_payload.get("title") or source_path.stem),
                duration=duration,
            )

        yt_dlp = yt_dlp_command()
        if yt_dlp is None:
            raise RuntimeError(
                "yt-dlp is not installed. Install the yt-dlp Python package, or switch to a local file source."
            )

        job.log("Downloading source with yt-dlp.")
        target = job_dir / "source.%(ext)s"
        video_command = yt_dlp + [
            *yt_dlp_cookie_file_args(self.root),
            "--sleep-requests",
            "1",
            "--sleep-interval",
            "1",
            "--max-sleep-interval",
            "3",
            "--extractor-args",
            yt_dlp_video_extractor_args(),
            *yt_dlp_js_args(),
            "--no-progress",
            "--write-info-json",
            "--merge-output-format",
            "mp4",
            "-f",
            yt_dlp_video_format(),
            *yt_dlp_ffmpeg_args(self.root),
            "-o",
            str(target),
            source_value,
        ]
        completed = self._run_yt_dlp_command(job, video_command, allow_browser_cookies=True)
        job.log(completed.stdout.strip() or "Download finished.")

        subtitle_command = yt_dlp + [
            *yt_dlp_cookie_file_args(self.root),
            "--sleep-requests",
            "1",
            "--sleep-interval",
            "1",
            "--max-sleep-interval",
            "3",
            "--extractor-args",
            yt_dlp_video_extractor_args(),
            *yt_dlp_js_args(),
            "--no-progress",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            subtitle_languages_for_request(request),
            "--sub-format",
            "vtt",
            "-o",
            str(target),
            source_value,
        ]
        try:
            subtitles_completed = self._run_yt_dlp_command(job, subtitle_command, allow_browser_cookies=False)
            subtitles_stdout = (subtitles_completed.stdout or "").strip()
            if subtitles_stdout:
                job.log("Source subtitles downloaded.")
        except RuntimeError as exc:
            job.log(str(exc))
            job.log("Continuing without source subtitles for this run.")

        media_matches = self._downloaded_media_files(job_dir, "source")
        if not media_matches:
            raise RuntimeError("Source download finished but no output file was created.")
        source_path = self._resolve_downloaded_media(
            job,
            sorted(media_matches),
            job_dir,
            merged_name="source_merged.mp4",
        )
        subtitle_path = self._pick_subtitle_path(
            list(job_dir.glob("source*.srt")) + list(job_dir.glob("source*.vtt")),
            request.get("language"),
        )
        info_path = next(iter(job_dir.glob("source.info.json")), None)
        source_path, info_path = self._upgrade_low_resolution_source(
            job,
            yt_dlp,
            source_value,
            source_path,
            info_path,
            job_dir,
        )
        info_payload = self._read_info_json(info_path) if info_path else {}
        duration = self._safe_float(info_payload.get("duration")) or self._probe_duration(job, source_path)
        return SourceBundle(
            source_path=source_path,
            subtitle_path=subtitle_path,
            info_path=info_path,
            title=str(info_payload.get("title") or source_path.stem),
            duration=duration,
        )

    def _downloaded_media_files(self, job_dir: Path, prefix: str) -> list[Path]:
        return [
            path
            for path in job_dir.glob(f"{prefix}*")
            if path.is_file()
            and path.suffix.lower() not in {".json", ".srt", ".vtt", ".part", ".temp"}
        ]

    def _upgrade_low_resolution_source(
        self,
        job: Any,
        yt_dlp: list[str],
        source_value: str,
        source_path: Path,
        info_path: Path | None,
        job_dir: Path,
    ) -> tuple[Path, Path | None]:
        width, height = self._probe_video_dimensions(job, source_path)
        if height is None:
            return source_path, info_path

        if height >= 720:
            job.log(f"Source quality ready at {width}x{height}.")
            return source_path, info_path

        job.log(f"Source quality is only {width}x{height}. Trying a higher-quality retry.")
        retry_target = job_dir / "source_hq.%(ext)s"
        retry_command = yt_dlp + [
            *yt_dlp_cookie_file_args(self.root),
            "--sleep-requests",
            "1",
            "--sleep-interval",
            "1",
            "--max-sleep-interval",
            "3",
            "--extractor-args",
            yt_dlp_video_extractor_args(),
            *yt_dlp_js_args(),
            "--no-progress",
            "--write-info-json",
            "--merge-output-format",
            "mp4",
            "-f",
            yt_dlp_video_format(),
            *yt_dlp_ffmpeg_args(self.root),
            "-o",
            str(retry_target),
            source_value,
        ]

        try:
            retry_completed = self._run_yt_dlp_command(job, retry_command, allow_browser_cookies=True)
            retry_stdout = (retry_completed.stdout or "").strip()
            if retry_stdout:
                job.log("High-quality retry finished.")
        except RuntimeError as exc:
            job.log(f"High-quality retry skipped: {exc}")
            return source_path, info_path

        retry_matches = self._downloaded_media_files(job_dir, "source_hq")
        if not retry_matches:
            job.log("High-quality retry did not produce a usable video file.")
            return source_path, info_path

        retry_source = self._resolve_downloaded_media(
            job,
            sorted(retry_matches),
            job_dir,
            merged_name="source_hq_merged.mp4",
        )
        retry_info = next(iter(job_dir.glob("source_hq.info.json")), None)
        retry_width, retry_height = self._probe_video_dimensions(job, retry_source)
        if retry_height is not None and retry_height > height:
            job.log(f"Using higher-quality source {retry_width}x{retry_height}.")
            return retry_source, retry_info or info_path

        for browser in yt_dlp_cookie_browser_candidates():
            browser_prefix = f"source_hq_{browser}"
            browser_target = job_dir / f"{browser_prefix}.%(ext)s"
            browser_command = yt_dlp + [
                "--cookies-from-browser",
                browser,
                "--sleep-requests",
                "1",
                "--sleep-interval",
                "1",
                "--max-sleep-interval",
                "3",
                "--extractor-args",
                yt_dlp_video_extractor_args(),
                *yt_dlp_js_args(),
                "--no-progress",
                "--write-info-json",
                "--merge-output-format",
                "mp4",
                "-f",
                yt_dlp_video_format(),
                *yt_dlp_ffmpeg_args(self.root),
                "-o",
                str(browser_target),
                source_value,
            ]
            job.log(f"Trying browser-backed high-quality retry with {browser}.")
            browser_completed = run_command(browser_command, env=runtime_env(self.root), check=False)
            if browser_completed.returncode != 0:
                job.log(yt_dlp_error_summary(browser_completed))
                continue

            browser_matches = self._downloaded_media_files(job_dir, browser_prefix)
            if not browser_matches:
                continue

            browser_source = self._resolve_downloaded_media(
                job,
                sorted(browser_matches),
                job_dir,
                merged_name=f"{browser_prefix}_merged.mp4",
            )
            browser_info = next(iter(job_dir.glob(f"{browser_prefix}.info.json")), None)
            browser_width, browser_height = self._probe_video_dimensions(job, browser_source)
            if browser_height is not None and browser_height > height:
                job.log(f"Using browser-backed source {browser_width}x{browser_height}.")
                return browser_source, browser_info or info_path

        job.log("Higher-quality retry did not improve the source stream, so the current source will be used.")
        return source_path, info_path

    def _resolve_downloaded_media(
        self,
        job: Any,
        media_matches: list[Path],
        job_dir: Path,
        *,
        merged_name: str,
    ) -> Path:
        stream_map = {path: self._media_streams(path) for path in media_matches}

        muxed_candidates = [
            path for path, streams in stream_map.items() if streams["video"] and streams["audio"]
        ]
        if muxed_candidates:
            return self._prefer_primary_media(muxed_candidates)

        video_candidates = [path for path, streams in stream_map.items() if streams["video"]]
        audio_candidates = [path for path, streams in stream_map.items() if streams["audio"]]

        if video_candidates and audio_candidates:
            ffmpeg = ffmpeg_command()
            if ffmpeg is None:
                raise RuntimeError(
                    "Downloaded separate video and audio streams, but ffmpeg is unavailable to merge them."
                )

            video_path = self._prefer_primary_media(video_candidates)
            audio_path = self._prefer_primary_media(audio_candidates)
            merged_path = job_dir / merged_name
            command = ffmpeg + [
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(merged_path),
            ]
            job.log("Merging separate video and audio streams into a single source file.")
            run_command(command)
            return merged_path

        if video_candidates:
            return self._prefer_primary_media(video_candidates)

        raise RuntimeError("The download produced no usable video stream.")

    def _prefer_primary_media(self, candidates: list[Path]) -> Path:
        def score(path: Path) -> tuple[int, int, str]:
            name = path.name.lower()
            is_fragment = 1 if re.search(r"\.f\d+\.", name) else 0
            is_mp4 = 0 if path.suffix.lower() == ".mp4" else 1
            return (is_fragment, is_mp4, name)

        return sorted(candidates, key=score)[0]

    def _media_streams(self, path: Path) -> dict[str, bool]:
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            suffix = path.suffix.lower()
            return {
                "video": suffix in {".mp4", ".mkv", ".mov", ".webm"},
                "audio": suffix in {".mp4", ".mkv", ".mov", ".webm", ".m4a", ".mp3"},
            }

        completed = run_command(ffmpeg + ["-i", str(path)], check=False)
        combined = f"{completed.stdout}\n{completed.stderr}"
        return {
            "video": " Video:" in combined,
            "audio": " Audio:" in combined,
        }

    def _build_segments(
        self,
        job: Any,
        request: dict[str, Any],
        bundle: SourceBundle,
        subtitle_entries: list[SubtitleEntry],
        job_dir: Path,
    ) -> tuple[list[Segment], dict[str, Any]]:
        if request["segments"]:
            job.log("Using explicit timestamps.")
            segments = []
            raw_segments = [value.strip() for value in request["segments"].split(",") if value.strip()]
            for raw in raw_segments:
                match = SEGMENT_PATTERN.match(raw)
                if not match:
                    raise ValueError(f"Bad segment format: {raw}")
                start = parse_seconds(match.group("start"))
                end = parse_seconds(match.group("end"))
                if end <= start:
                    raise ValueError(f"Segment end must be later than start: {raw}")
                segments.append(Segment(start=start, end=end, strategy="manual"))
            return segments, {
                "method": "manual",
                "selected_segments": [segment.as_dict() for segment in segments],
                "top_candidates": [],
                "subtitle_source": bundle.subtitle_path.name if bundle.subtitle_path else None,
            }

        duration = bundle.duration or self._probe_duration(job, bundle.source_path)
        clip_duration = float(request["clip_duration_sec"])
        clips_count = int(request["clips_count"])
        selection_offset = int(request.get("selection_offset") or 0)

        if subtitle_entries and duration:
            job.log("Analyzing subtitle density and audio energy to find highlight moments.")
            segments, analysis = self._build_segments_from_subtitles(
                bundle.source_path,
                subtitle_entries,
                duration,
                clip_duration,
                clips_count,
                selection_offset,
                bundle.subtitle_path,
            )
            if segments:
                return segments, analysis

        if duration:
            job.log("Falling back to audio-driven highlight analysis.")
            segments, analysis = self._build_segments_from_audio(
                job,
                request,
                bundle.source_path,
                bundle.title or "",
                duration,
                clip_duration,
                clips_count,
                selection_offset,
                job_dir,
            )
            if segments:
                return segments, analysis

        job.log("Duration probing failed. Falling back to a single first-segment plan.")
        segment = Segment(
            start=0.0,
            end=clip_duration,
            strategy="fallback",
            reason="Duration metadata was unavailable, so the workflow used the opening window.",
        )
        return [segment], {
            "method": "fallback",
            "selected_segments": [segment.as_dict()],
            "top_candidates": [],
            "subtitle_source": bundle.subtitle_path.name if bundle.subtitle_path else None,
        }

    def _probe_duration(self, job: Any, source_path: Path) -> float | None:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            command = [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source_path),
            ]
            try:
                completed = run_command(command)
                return float(completed.stdout.strip())
            except Exception:
                job.log("ffprobe failed; falling back to ffmpeg duration parsing.")

        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            return None

        completed = run_command(ffmpeg + ["-i", str(source_path)], check=False)
        combined = f"{completed.stdout}\n{completed.stderr}"
        match = DURATION_PATTERN.search(combined)
        if not match:
            return None
        try:
            return parse_clock_seconds(match.group(1))
        except Exception:
            return None

    def _probe_video_dimensions(self, job: Any, source_path: Path) -> tuple[int | None, int | None]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            command = [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(source_path),
            ]
            try:
                completed = run_command(command)
                raw = completed.stdout.strip()
                if "x" in raw:
                    width, height = raw.split("x", 1)
                    return int(width), int(height)
            except Exception:
                job.log("ffprobe resolution check failed; falling back to ffmpeg parsing.")

        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            return None, None

        completed = run_command(ffmpeg + ["-i", str(source_path)], check=False)
        combined = f"{completed.stdout}\n{completed.stderr}"
        match = VIDEO_STREAM_PATTERN.search(combined)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    def _render_segments(
        self,
        job: Any,
        request: dict[str, Any],
        source_path: Path,
        segments: list[Segment],
        job_dir: Path,
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            job.log("ffmpeg is not installed. Skipping render and writing a manifest only.")
            return outputs

        for index, segment in enumerate(segments, start=1):
            output_path = job_dir / f"clip_{index:02d}_vertical.mp4"
            command = self._build_vertical_render_command(source_path, segment, output_path)
            job.log(
                f"Rendering highlight {index}/{len(segments)} from {seconds_to_clock(segment.start)} "
                f"to {seconds_to_clock(segment.end)}."
            )
            run_command(command)
            outputs.append(
                {
                    "label": f"Recommended Clip {index}",
                    "path": output_path,
                    "segment": segment.as_dict(),
                    "segment_obj": segment,
                }
            )
        return outputs

    def _caption_outputs(
        self,
        job: Any,
        request: dict[str, Any],
        source_path: Path,
        clip_outputs: list[dict[str, Any]],
        job_dir: Path,
        subtitle_entries: list[SubtitleEntry],
    ) -> list[dict[str, Any]]:
        if not clip_outputs:
            return clip_outputs

        if not request["add_captions"]:
            job.log("Captions disabled by request.")
            return clip_outputs

        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            job.log("ffmpeg not found, so caption burn-in is not available.")
            return clip_outputs

        final_outputs: list[dict[str, Any]] = []
        whisper = whisper_command()
        for index, clip in enumerate(clip_outputs, start=1):
            clip_path = clip["path"]
            segment = clip["segment_obj"]

            subtitle_path: Path | None = None
            subtitle_label = "source"

            if subtitle_entries:
                fallback_subtitle_path = job_dir / f"{clip_path.stem}.srt"
                written = self._write_segment_subtitles(subtitle_entries, segment, fallback_subtitle_path)
                if written:
                    subtitle_path = fallback_subtitle_path

            if subtitle_path is None and whisper is not None:
                subtitle_label = "Whisper"
                job.log(f"Transcribing highlight {index} with Whisper ({request['whisper_model']}).")
                transcribe_command = whisper + [
                    str(clip_path),
                    "--model",
                    request["whisper_model"],
                    "--model_dir",
                    str(self.model_root),
                    "--task",
                    "transcribe",
                    "--output_format",
                    "srt",
                    "--output_dir",
                    str(job_dir),
                    "--condition_on_previous_text",
                    "False",
                    "--fp16",
                    "False",
                    "--word_timestamps",
                    "True",
                    "--max_line_width",
                    "18",
                    "--max_line_count",
                    "2",
                    "--verbose",
                    "False",
                ]
                if request["language"] and request["language"].lower() != "auto":
                    transcribe_command.extend(["--language", request["language"]])

                completed = run_command(transcribe_command, check=False, env=runtime_env(self.root))
                if completed.returncode != 0:
                    whisper_error = (completed.stderr or completed.stdout or "").strip()
                    if whisper_error:
                        job.log(whisper_error.splitlines()[-1])
                    job.log(f"Whisper transcription failed for {clip_path.name}; falling back if possible.")
                else:
                    subtitle_path = job_dir / f"{clip_path.stem}.srt"
                    if subtitle_path.exists():
                        if not self._stylize_subtitle_file(subtitle_path, str(request.get("language") or "auto")):
                            subtitle_path = None

            if subtitle_path is None and openai_transcription_available():
                subtitle_label = "OpenAI"
                subtitle_path = self._transcribe_clip_with_openai(job, clip_path, segment, request, job_dir)

            if subtitle_path is None:
                subtitle_label = "hook"
                subtitle_path = self._write_hook_subtitles(job, clip_path, segment, request, job_dir)

            if subtitle_path is None or not subtitle_path.exists():
                job.log(f"No subtitle file produced for {clip_path.name}; keeping plain clip.")
                final_outputs.append(clip)
                continue

            final_path = job_dir / f"{clip_path.stem}_captioned.mp4"
            target_fps = requested_output_fps(request)
            if target_fps is not None:
                job.log(f"Applying motion-smoothing to {target_fps} fps for highlight {index}.")
            burn_command = self._build_caption_overlay_command(
                clip_path,
                subtitle_path,
                final_path,
                request,
            )
            job.log(f"Burning {subtitle_label} subtitles into highlight {index}.")
            run_command(burn_command)
            final_outputs.append(
                {
                    "label": f"Captioned Clip {index}",
                    "path": final_path,
                    "segment": clip["segment"],
                    "segment_obj": segment,
                    "subtitle_path": subtitle_path,
                }
            )
        return final_outputs

    def _vertical_filter_chain(self, subtitle_path: Path | None = None) -> str:
        filters = [
            (
                "split=2[bg][fg];"
                "[bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=1080:1920,gblur=sigma=28,eq=brightness=-0.04:saturation=1.08[bg];"
                "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
                "setsar=1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                "eq=contrast=1.03:saturation=1.05,"
                "unsharp=5:5:0.55:5:5:0.05,setsar=1,format=yuv420p"
            )
        ]
        if subtitle_path is not None:
            filters.append(ffmpeg_subtitles_filter(subtitle_path))
        return ",".join(filters)

    def _build_vertical_render_command(
        self,
        source_path: Path,
        segment: Segment,
        output_path: Path,
        *,
        subtitle_path: Path | None = None,
    ) -> list[str]:
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is not installed.")
        return ffmpeg + [
            "-y",
            "-ss",
            seconds_to_ffmpeg_time(segment.start),
            "-i",
            str(source_path),
            "-t",
            f"{max(1.0, segment.duration):.3f}",
            "-vf",
            self._vertical_filter_chain(subtitle_path),
            "-map",
            "0:v:0?",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "14",
            "-maxrate",
            "18M",
            "-bufsize",
            "24M",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    def _caption_overlay_filter_chain(self, subtitle_path: Path, request: dict[str, Any]) -> str:
        filters: list[str] = []
        target_fps = requested_output_fps(request)
        if target_fps is not None:
            filters.append(
                f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
            )
        filters.append(ffmpeg_subtitles_filter(subtitle_path))
        return ",".join(filters)

    def _build_caption_overlay_command(
        self,
        clip_path: Path,
        subtitle_path: Path,
        output_path: Path,
        request: dict[str, Any],
    ) -> list[str]:
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is not installed.")

        target_fps = requested_output_fps(request)
        return ffmpeg + [
            "-y",
            "-i",
            str(clip_path),
            "-vf",
            self._caption_overlay_filter_chain(subtitle_path, request),
            "-map",
            "0:v:0?",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "14",
            *([] if target_fps is None else ["-r", str(target_fps)]),
            "-maxrate",
            "18M",
            "-bufsize",
            "24M",
            "-profile:v",
            "high",
            "-level",
            "4.2",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    def _transcribe_clip_with_openai(
        self,
        job: Any,
        clip_path: Path,
        segment: Segment,
        request: dict[str, Any],
        job_dir: Path,
    ) -> Path | None:
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            return None

        audio_path = job_dir / f"{clip_path.stem}_audio.mp3"
        subtitle_path = job_dir / f"{clip_path.stem}.srt"
        extract_command = ffmpeg + [
            "-y",
            "-i",
            str(clip_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(audio_path),
        ]

        try:
            run_command(extract_command)
        except Exception as exc:
            job.log(f"OpenAI transcription audio extract failed for {clip_path.name}: {exc}")
            return None

        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            job.log(f"OpenAI transcription skipped for {clip_path.name}: no audio was extracted.")
            return None

        if audio_path.stat().st_size > 24 * 1024 * 1024:
            job.log(f"OpenAI transcription skipped for {clip_path.name}: audio file is larger than 24 MB.")
            return None

        try:
            from openai import OpenAI

            client = OpenAI()
            model = openai_transcription_model()
            params: dict[str, Any] = {
                "file": audio_path.open("rb"),
                "model": model,
                "response_format": "srt" if model == "whisper-1" else "text",
            }
            language = str(request.get("language") or "auto").strip().lower()
            if language and language != "auto":
                params["language"] = language

            job.log(f"Transcribing highlight with OpenAI ({model}).")
            try:
                response = client.audio.transcriptions.create(**params)
            finally:
                params["file"].close()
        except Exception as exc:
            job.log(f"OpenAI transcription failed for {clip_path.name}: {exc}")
            return None

        if isinstance(response, str):
            transcript = response
        elif hasattr(response, "text"):
            transcript = str(response.text)
        else:
            transcript = str(response)

        transcript = transcript.strip()
        if not transcript:
            return None

        if "-->" in transcript:
            subtitle_path.write_text(transcript + "\n", encoding="utf-8")
        else:
            subtitle_path.write_text(
                "\n".join(
                    [
                        "1",
                        f"{seconds_to_srt_time(0)} --> {seconds_to_srt_time(max(1.0, segment.duration))}",
                        clean_caption_text(transcript),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

        if not self._stylize_subtitle_file(subtitle_path, str(request.get("language") or "auto")):
            job.log(f"OpenAI transcription for {clip_path.name} was not usable as subtitles.")
            return None
        return subtitle_path

    def _write_hook_subtitles(
        self,
        job: Any,
        clip_path: Path,
        segment: Segment,
        request: dict[str, Any],
        job_dir: Path,
    ) -> Path | None:
        duration = max(1.0, segment.duration)
        if duration < 2.0:
            return None

        subtitle_path = job_dir / f"{clip_path.stem}_hook.srt"
        first_end = min(duration, 2.8)
        second_start = min(duration - 0.2, first_end + 0.25)
        second_end = min(duration, second_start + 2.8)
        cues = [
            (0.0, first_end, "Wait for it 😂"),
        ]
        if second_end - second_start >= 1.0:
            cues.append((second_start, second_end, "This part is wild"))

        lines: list[str] = []
        for index, (start, end, text) in enumerate(cues, start=1):
            lines.extend(
                [
                    str(index),
                    f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}",
                    wrap_caption_text(text, max_line_chars=18, max_lines=2),
                    "",
                ]
            )
        subtitle_path.write_text("\n".join(lines), encoding="utf-8")
        job.log(f"No usable speech subtitles for {clip_path.name}; adding a short hook overlay.")
        return subtitle_path

    def _build_metadata(
        self,
        job: Any,
        request: dict[str, Any],
        outputs: list[dict[str, Any]],
        segments: list[Segment],
        analysis: dict[str, Any],
        bundle: SourceBundle,
        job_dir: Path,
    ) -> Path:
        topic = request["topic"] or slugify(request["project_name"]).replace("-", " ")
        source_title = bundle.title or topic
        captions = []
        for index, segment in enumerate(segments, start=1):
            if segment.excerpt:
                captions.append(f"Part {index}: {segment.excerpt}")
        if not captions:
            captions = [f"{source_title} highlight {index}" for index in range(1, len(segments) + 1)]

        hashtags = request.get("hashtags") or self._hashtags(topic)
        caption_hint = str(request.get("caption_hint") or "").strip()
        if not caption_hint:
            caption_hint = " ".join(str(tag) for tag in hashtags if str(tag).strip()).strip()

        payload = {
            "topic": topic,
            "source_title": source_title,
            "publish_mode": request["publish_mode"],
            "captions": captions,
            "hashtags": hashtags,
            "caption_hint": caption_hint,
            "analysis_method": analysis.get("method"),
            "segments": [segment.as_dict() for segment in segments],
            "outputs": [str(item["path"].name) for item in outputs],
            "notes": [
                "Use only content you own or are licensed to edit and publish.",
                "This build finds likely highlight windows using subtitles and audio when available.",
                "Viral performance is never guaranteed; this workflow automates packaging and selection.",
            ],
        }
        metadata_path = job_dir / "metadata.json"
        metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        job.log("Metadata ideas generated.")
        return metadata_path

    def _build_upload_notes(
        self,
        job: Any,
        request: dict[str, Any],
        outputs: list[dict[str, Any]],
        analysis: dict[str, Any],
        bundle: SourceBundle,
        job_dir: Path,
    ) -> None:
        tiktok_ready = (
            request["publish_mode"] == "tiktok_api"
            and bool(os.getenv("TIKTOK_ACCESS_TOKEN"))
            and bool(os.getenv("TIKTOK_OPEN_ID"))
        )

        lines = [
            "# Next Steps",
            "",
            "This project produces local short-form highlight outputs with ranked analysis.",
            "",
            "## Highlight selection",
            "",
            f"- Method used: {analysis.get('method', 'unknown')}",
            f"- Subtitle source: {analysis.get('subtitle_source') or 'none'}",
            f"- Source title: {bundle.title or bundle.source_path.stem}",
            "",
            "## Important limits",
            "",
            "- This build does not automate watermark removal.",
            "- TikTok posting depends on TikTok developer approval, OAuth scopes, and app review state.",
            "- The app can rank likely highlights, but you should still review the selected moments before posting.",
            "",
            "## Current output",
            "",
        ]

        caption_hint = str(request.get("caption_hint") or "").strip()
        if caption_hint:
            lines.extend(["## Caption", "", caption_hint, ""])

        if outputs:
            for item in outputs:
                lines.append(f"- {item['path'].name}")
        else:
            lines.append("- No rendered clips yet. Install ffmpeg to enable video rendering.")

        lines.extend(
            [
                "",
                "## TikTok mode",
                "",
                f"- Selected mode: {request['publish_mode']}",
                f"- API credentials detected: {'yes' if tiktok_ready else 'no'}",
                "",
                "Official reference:",
                "- https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content",
            ]
        )

        notes_path = job_dir / "NEXT_STEPS.md"
        notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        job.log("Next-step notes written.")

    def _build_segments_from_subtitles(
        self,
        source_path: Path,
        subtitle_entries: list[SubtitleEntry],
        duration: float,
        clip_duration: float,
        clips_count: int,
        selection_offset: int,
        subtitle_path: Path | None,
    ) -> tuple[list[Segment], dict[str, Any]]:
        max_start = max(0.0, duration - clip_duration)
        step = max(4.0, min(12.0, clip_duration / 3))
        candidate_starts: set[float] = set()

        for entry in subtitle_entries:
            start = clamp(entry.start - min(6.0, clip_duration * 0.2), 0.0, max_start)
            snapped = round(start / step) * step
            candidate_starts.add(round(clamp(snapped, 0.0, max_start), 2))

        candidates: list[dict[str, Any]] = []
        for start in sorted(candidate_starts):
            end = min(duration, start + clip_duration)
            window_entries = [
                entry for entry in subtitle_entries if overlaps(entry.start, entry.end, start, end)
            ]
            if not window_entries:
                continue

            combined_text = " ".join(clean_caption_text(entry.text) for entry in window_entries).strip()
            if not combined_text:
                continue

            words = re.findall(r"[\w']+", combined_text, flags=re.UNICODE)
            word_count = len(words)
            cue_count = len(window_entries)
            punctuation_hits = len(re.findall(r"[!?]", combined_text)) + combined_text.count("...")
            density_score = clamp(word_count / max(28.0, clip_duration * 2.3), 0.0, 1.4)
            cue_score = clamp(cue_count / max(4.0, clip_duration / 4.5), 0.0, 1.0)
            punctuation_score = clamp(punctuation_hits / 6.0, 0.0, 1.0)
            edge_penalty = 0.12 if start < min(clip_duration, duration * 0.08) else 0.0
            transcript_score = (0.58 * density_score) + (0.24 * punctuation_score) + (0.18 * cue_score)
            transcript_score = clamp(transcript_score - edge_penalty, 0.0, 1.6)

            candidates.append(
                {
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "transcript_score": round(transcript_score, 4),
                    "audio_score": 0.0,
                    "score": round(transcript_score, 4),
                    "word_count": word_count,
                    "cue_count": cue_count,
                    "excerpt": self._excerpt_from_entries(window_entries),
                    "reason": "High dialogue density window",
                }
            )

        if not candidates:
            return [], {
                "method": "subtitles",
                "selected_segments": [],
                "top_candidates": [],
                "subtitle_source": subtitle_path.name if subtitle_path else None,
            }

        for candidate in sorted(candidates, key=lambda item: item["transcript_score"], reverse=True)[:12]:
            audio_score = self._audio_window_score(
                source_path,
                float(candidate["start"]),
                float(candidate["end"]),
            )
            candidate["audio_score"] = round(audio_score, 4)
            candidate["score"] = round((candidate["transcript_score"] * 0.72) + (audio_score * 0.28), 4)
            candidate["reason"] = (
                f"Dialogue score {candidate['transcript_score']:.2f}, "
                f"audio score {candidate['audio_score']:.2f}"
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        segments = self._select_segments(candidates, clips_count, clip_duration, "auto-highlight", selection_offset)
        return segments, {
            "method": "subtitles_plus_audio",
            "subtitle_source": subtitle_path.name if subtitle_path else None,
            "selected_segments": [segment.as_dict() for segment in segments],
            "top_candidates": candidates[:12],
        }

    def _build_segments_from_audio(
        self,
        job: Any,
        request: dict[str, Any],
        source_path: Path,
        source_title: str,
        duration: float,
        clip_duration: float,
        clips_count: int,
        selection_offset: int,
        job_dir: Path,
    ) -> tuple[list[Segment], dict[str, Any]]:
        max_start = max(0.0, duration - clip_duration)
        stride = max(12.0, clip_duration * 0.6)
        candidate_starts = []
        current = 0.0
        while current <= max_start + 0.01:
            candidate_starts.append(round(current, 2))
            current += stride
        if not candidate_starts:
            candidate_starts = [0.0]

        candidates: list[dict[str, Any]] = []
        for start in candidate_starts:
            end = min(duration, start + clip_duration)
            audio_score = self._audio_window_score(source_path, start, end)
            center = start + ((end - start) / 2)
            center_bias = 1.0 - abs((center / max(duration, 1.0)) - 0.5)
            center_score = clamp(center_bias, 0.0, 1.0)
            candidates.append(
                {
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "transcript_score": 0.0,
                    "audio_score": round(audio_score, 4),
                    "score": round((audio_score * 0.82) + (center_score * 0.18), 4),
                    "word_count": 0,
                    "cue_count": 0,
                    "excerpt": "",
                    "reason": f"Audio energy score {audio_score:.2f}",
                }
            )

        candidates.sort(key=lambda item: item["score"], reverse=True)
        method = "audio_only"
        if self._score_audio_candidates_with_openai(
            job,
            request,
            source_path,
            source_title,
            candidates,
            clip_duration,
            selection_offset + clips_count,
            job_dir,
        ):
            method = "audio_plus_openai_dialogue"
            candidates.sort(key=lambda item: item["score"], reverse=True)

        strategy = "dialogue-highlight" if method == "audio_plus_openai_dialogue" else "audio-highlight"
        segments = self._select_segments(candidates, clips_count, clip_duration, strategy, selection_offset)
        return segments, {
            "method": method,
            "subtitle_source": None,
            "selected_segments": [segment.as_dict() for segment in segments],
            "top_candidates": candidates[:12],
        }

    def _score_audio_candidates_with_openai(
        self,
        job: Any,
        request: dict[str, Any],
        source_path: Path,
        source_title: str,
        candidates: list[dict[str, Any]],
        clip_duration: float,
        target_count: int,
        job_dir: Path,
    ) -> bool:
        if not candidates or not openai_transcription_available():
            return False

        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            return False

        probe_count = min(len(candidates), max(10, min(24, target_count * 4)))
        probe_dir = job_dir / "candidate_transcripts"
        probe_dir.mkdir(parents=True, exist_ok=True)

        try:
            from openai import OpenAI

            client = OpenAI()
        except Exception as exc:
            job.log(f"OpenAI dialogue probing skipped: {exc}")
            return False

        model = openai_transcription_model()
        language = str(request.get("language") or "auto").strip().lower()
        prefer_cyrillic = bool(re.search(r"[\u0400-\u04FF]", source_title))
        useful = 0
        job.log(f"Checking {probe_count} candidate window(s) for real dialogue with OpenAI.")

        for index, candidate in enumerate(candidates[:probe_count], start=1):
            start = float(candidate["start"])
            end = float(candidate["end"])
            audio_path = probe_dir / f"candidate_{index:02d}.mp3"
            extract_command = ffmpeg + [
                "-y",
                "-ss",
                seconds_to_ffmpeg_time(start),
                "-i",
                str(source_path),
                "-t",
                f"{max(1.0, end - start):.3f}",
                "-vn",
                "-sn",
                "-dn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "64k",
                str(audio_path),
            ]
            completed = run_command(extract_command, check=False)
            if completed.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size <= 0:
                candidate["reason"] = f"{candidate.get('reason') or 'Audio window'}; dialogue probe failed"
                candidate["score"] = round(float(candidate["score"]) * 0.25, 4)
                continue

            try:
                params: dict[str, Any] = {
                    "file": audio_path.open("rb"),
                    "model": model,
                    "response_format": "text",
                }
                if language and language != "auto":
                    params["language"] = language
                try:
                    response = client.audio.transcriptions.create(**params)
                finally:
                    params["file"].close()
            except Exception as exc:
                candidate["reason"] = f"{candidate.get('reason') or 'Audio window'}; dialogue probe failed"
                candidate["score"] = round(float(candidate["score"]) * 0.25, 4)
                job.log(f"Dialogue probe failed for candidate {index}: {exc}")
                continue

            transcript = clean_caption_text(str(response.text if hasattr(response, "text") else response))
            transcript = re.sub(r"\s+", " ", transcript).strip()
            word_count = caption_word_count(transcript)
            candidate["word_count"] = word_count
            candidate["cue_count"] = 0
            candidate["excerpt"] = transcript[:180].rstrip()
            has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", transcript))

            if prefer_cyrillic and not has_cyrillic:
                candidate["transcript_score"] = 0.0
                candidate["score"] = round(float(candidate["audio_score"]) * 0.12, 4)
                candidate["reason"] = "Rejected non-Cyrillic transcript for Cyrillic source"
                continue

            if word_count < 3 or is_low_quality_caption(transcript):
                candidate["transcript_score"] = 0.0
                candidate["score"] = round(float(candidate["audio_score"]) * 0.18, 4)
                candidate["reason"] = "Rejected low-dialogue or numeric-only transcript"
                continue

            punctuation_hits = len(re.findall(r"[!?]", transcript)) + transcript.count("...")
            density_score = clamp(word_count / max(18.0, clip_duration * 1.15), 0.0, 1.4)
            punctuation_score = clamp(punctuation_hits / 4.0, 0.0, 1.0)
            transcript_score = clamp((density_score * 0.86) + (punctuation_score * 0.14), 0.0, 1.45)
            audio_score = float(candidate["audio_score"])
            candidate["transcript_score"] = round(transcript_score, 4)
            candidate["score"] = round((transcript_score * 0.78) + (audio_score * 0.22), 4)
            candidate["reason"] = f"Dialogue probe {word_count} words, audio score {audio_score:.2f}"
            useful += 1

        if useful <= 0:
            return False

        for candidate in candidates[probe_count:]:
            candidate["score"] = round(float(candidate["score"]) * 0.22, 4)
            candidate["reason"] = f"{candidate.get('reason') or 'Audio window'}; not dialogue-probed"
        return True

    def _load_subtitles(self, subtitle_path: Path | None) -> list[SubtitleEntry]:
        if subtitle_path is None or not subtitle_path.exists():
            return []

        text = subtitle_path.read_text(encoding="utf-8", errors="ignore")
        blocks = re.split(r"\r?\n\r?\n+", text)
        entries: list[SubtitleEntry] = []
        for block in blocks:
            lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            if lines[0].upper().startswith("WEBVTT"):
                continue

            time_line = next((line for line in lines if "-->" in line), None)
            if time_line is None:
                continue

            start_raw, end_raw = [part.strip().split(" ")[0] for part in time_line.split("-->")[:2]]
            try:
                start = parse_clock_seconds(start_raw)
                end = parse_clock_seconds(end_raw)
            except Exception:
                continue

            text_lines = [line for line in lines if line != time_line and not line.isdigit()]
            caption = clean_caption_text(" ".join(text_lines))
            if not caption or is_low_quality_caption(caption):
                continue
            entries.append(SubtitleEntry(start=start, end=end, text=caption))
        return entries

    def _write_segment_subtitles(self, entries: list[SubtitleEntry], segment: Segment, target: Path) -> int:
        overlapping_entries = []
        language_hint = "auto"
        for entry in entries:
            if not overlaps(entry.start, entry.end, segment.start, segment.end):
                continue
            start, end = retime_subtitle_window(
                entry.start - segment.start,
                entry.end - segment.start,
                segment.duration,
            )
            if end <= start:
                continue
            if contains_cyrillic(entry.text):
                language_hint = "ru"
            overlapping_entries.append(SubtitleEntry(start=start, end=end, text=entry.text))

        grouped_entries = group_subtitle_entries(overlapping_entries)

        if not grouped_entries:
            return 0

        lines = []
        last_end = 0.0
        for index, entry in enumerate(grouped_entries, start=1):
            start = max(entry.start, last_end + (0.02 if index > 1 else 0.0))
            end = max(start + 0.28, entry.end)
            if end > segment.duration:
                end = segment.duration
                start = min(start, max(0.0, end - 0.28))
            display_text = decorate_subtitle_text(entry.text, index, language_hint)
            lines.extend(
                [
                    str(index),
                    f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}",
                    display_text,
                    "",
                ]
            )
            last_end = end
        target.write_text("\n".join(lines), encoding="utf-8")
        return len(grouped_entries)

    def _stylize_subtitle_file(self, subtitle_path: Path, language_hint: str) -> bool:
        entries = [
            entry
            for entry in self._load_subtitles(subtitle_path)
            if not is_low_quality_caption(clean_caption_text(entry.text))
        ]
        if not entries:
            try:
                subtitle_path.unlink()
            except Exception:
                subtitle_path.write_text("", encoding="utf-8")
            return False

        effective_hint = language_hint
        if effective_hint == "auto" and any(contains_cyrillic(entry.text) for entry in entries):
            effective_hint = "ru"

        lines = []
        last_end = 0.0
        for index, entry in enumerate(entries, start=1):
            start, end = retime_subtitle_window(entry.start, entry.end)
            start = max(start, last_end + (0.02 if index > 1 else 0.0))
            end = max(start + 0.28, end)
            lines.extend(
                [
                    str(index),
                    f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}",
                    decorate_subtitle_text(entry.text, index, effective_hint),
                    "",
                ]
            )
            last_end = end
        subtitle_path.write_text("\n".join(lines), encoding="utf-8")
        return True

    def _pick_subtitle_path(
        self,
        candidates: list[Path],
        language_hint: str | None = None,
    ) -> Path | None:
        if not candidates:
            return None

        preferred = str(language_hint or "auto").strip().lower()

        def score(path: Path) -> tuple[int, int]:
            name = path.name.lower()
            rank = 0
            if "live_chat" in name:
                rank -= 100
            if name.endswith(".srt"):
                rank += 10
            if preferred.startswith("ru") and ".ru" in name:
                rank += 20
            if preferred.startswith("en") and ".en" in name:
                rank += 20
            if ".en" in name:
                rank += 8
            if ".ru" in name:
                rank += 5
            return (-rank, len(name))

        return sorted(candidates, key=score)[0]

    def _find_local_subtitle(self, source_path: Path, language_hint: str | None = None) -> Path | None:
        candidates = list(source_path.parent.glob(f"{source_path.stem}*.srt"))
        candidates.extend(source_path.parent.glob(f"{source_path.stem}*.vtt"))
        return self._pick_subtitle_path(list(candidates), language_hint)

    def _read_info_json(self, info_path: Path) -> dict[str, Any]:
        try:
            return json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _audio_window_score(self, source_path: Path, start: float, end: float) -> float:
        ffmpeg = ffmpeg_command()
        if ffmpeg is None:
            return 0.0

        sample_duration = min(12.0, max(4.0, end - start))
        sample_start = start + max(0.0, ((end - start) - sample_duration) / 2)
        command = ffmpeg + [
            "-hide_banner",
            "-ss",
            seconds_to_ffmpeg_time(sample_start),
            "-t",
            f"{sample_duration:.2f}",
            "-i",
            str(source_path),
            "-af",
            "volumedetect",
            "-vn",
            "-sn",
            "-dn",
            "-f",
            "null",
            "-",
        ]
        completed = run_command(command, check=False)
        combined = f"{completed.stdout}\n{completed.stderr}"
        mean_match = MEAN_VOLUME_PATTERN.search(combined)
        max_match = MAX_VOLUME_PATTERN.search(combined)
        mean_db = float(mean_match.group(1)) if mean_match else -60.0
        max_db = float(max_match.group(1)) if max_match else -20.0
        mean_score = clamp((60.0 + mean_db) / 60.0, 0.0, 1.0)
        max_score = clamp((20.0 + max_db) / 20.0, 0.0, 1.0)
        return round((mean_score * 0.68) + (max_score * 0.32), 4)

    def _excerpt_from_entries(self, entries: list[SubtitleEntry]) -> str:
        parts = []
        for entry in entries[:3]:
            text = clean_caption_text(entry.text)
            if text:
                parts.append(text)
        excerpt = " ".join(parts).strip()
        excerpt = re.sub(r"\s+", " ", excerpt)
        return excerpt[:180].rstrip()

    def _select_segments(
        self,
        candidates: list[dict[str, Any]],
        clips_count: int,
        clip_duration: float,
        strategy: str,
        selection_offset: int = 0,
    ) -> list[Segment]:
        selected: list[Segment] = []
        min_gap = max(4.0, clip_duration * 0.35)
        target_count = selection_offset + clips_count

        for candidate in candidates:
            start = float(candidate["start"])
            end = float(candidate["end"])
            allowed = True
            for existing in selected:
                if overlaps(start - min_gap, end + min_gap, existing.start, existing.end):
                    allowed = False
                    break
            if not allowed:
                continue

            selected.append(
                Segment(
                    start=start,
                    end=end,
                    score=float(candidate["score"]),
                    strategy=strategy,
                    reason=str(candidate.get("reason") or ""),
                    excerpt=str(candidate.get("excerpt") or ""),
                )
            )
            if len(selected) >= target_count:
                break

        selected = selected[selection_offset:target_count]
        return sorted(selected, key=lambda item: item.start)

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _hashtags(self, topic: str) -> list[str]:
        words = [word for word in re.split(r"\W+", topic.lower()) if word]
        tags = ["#tiktok", "#shortvideo", "#fyp"]
        tags.extend(f"#{word}" for word in words[:5])
        unique: list[str] = []
        seen = set()
        for tag in tags:
            if tag not in seen:
                unique.append(tag)
                seen.add(tag)
        return unique

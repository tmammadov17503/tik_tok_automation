from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MIN_STORY_SECONDS = 64.0
THUMBNAIL_OUTRO_SECONDS = 0.65
RENDER_VERSION = "tiktok_story_reel_v1"

BG = "0x070911"
PANEL = "0x101820"
GREEN = "0x27e46f"
CYAN = "0x39c5ff"
AMBER = "0xffc857"
RED = "0xff4d5e"
WHITE = "white"
MUTED = "0xd8dde5"

AI_STORY_DISABLED_VALUES = {"0", "false", "no", "off"}
GENRE_ROTATION = [
    "eerie historical mystery",
    "forgotten disaster story",
    "folklore horror legend",
    "ancient mystery",
    "strange true history",
    "survival story",
    "lost place mystery",
    "dark biography",
]

TOPIC_LIBRARY: list[dict[str, str]] = [
    {
        "slug": "mosaddegh-1953-iran",
        "short_title": "REMOVED FOR OIL",
        "title": "The Prime Minister Removed For Oil",
        "figure": "Mohammad Mosaddegh",
        "role": "Iran's elected prime minister",
        "year": "1953",
        "place": "Tehran, Iran",
        "mission": "he tried to nationalize oil so more of the wealth stayed inside Iran",
        "pressure": "Britain lost control of a giant oil prize, and Cold War fear made the crisis even sharper",
        "turn": "a coup destroyed his government and the old power structure returned",
        "aftermath": "the argument over oil became one of the most important betrayals in modern Middle Eastern history",
        "hook": "He tried to take back his country's oil. Then oil helped remove him from power.",
    },
    {
        "slug": "lumumba-1961-congo",
        "short_title": "SILENCED AFTER FREEDOM",
        "title": "The Prime Minister Silenced After Independence",
        "figure": "Patrice Lumumba",
        "role": "Congo's first prime minister after independence",
        "year": "1960",
        "place": "Leopoldville, Congo",
        "mission": "he wanted the new country to speak for itself after Belgian colonial rule",
        "pressure": "mutiny, secession, foreign fear, and Cold War pressure turned independence into a trap",
        "turn": "he was arrested, transferred to enemies, and killed before his movement could stabilize",
        "aftermath": "his death turned him into a symbol of a country punished for asking to stand alone",
        "hook": "He helped give Congo a voice. Then that voice was silenced almost immediately.",
    },
    {
        "slug": "sankara-1987-burkina-faso",
        "short_title": "BETRAYED BY REVOLUTION",
        "title": "The President Betrayed By His Revolution",
        "figure": "Thomas Sankara",
        "role": "Burkina Faso's revolutionary president",
        "year": "1987",
        "place": "Ouagadougou, Burkina Faso",
        "mission": "he pushed vaccines, literacy, women's rights, anti-corruption, and self-reliance",
        "pressure": "his reforms moved too fast for elites who benefited from the old system",
        "turn": "allies turned against him, a coup hit, and Sankara was killed",
        "aftermath": "his unfinished revolution became a blueprint people still argue about today",
        "hook": "He renamed a country and tried to remake it. Then his own revolution turned on him.",
    },
    {
        "slug": "arbenz-1954-guatemala",
        "short_title": "OVERTHROWN FOR LAND",
        "title": "The President Overthrown For Bananas",
        "figure": "Jacobo Arbenz",
        "role": "Guatemala's reformist president",
        "year": "1954",
        "place": "Guatemala City, Guatemala",
        "mission": "he tried to move unused land from a powerful company to farmers",
        "pressure": "the reform threatened United Fruit holdings and became framed as a Cold War danger",
        "turn": "a CIA-backed operation and military pressure forced him out",
        "aftermath": "one land reform helped open decades of instability and violence",
        "hook": "He tried to give land to farmers. A fruit company helped make him a target.",
    },
    {
        "slug": "allende-1973-chile",
        "short_title": "BALLOTS TO BOMBS",
        "title": "The President Who Would Not Resign",
        "figure": "Salvador Allende",
        "role": "Chile's elected socialist president",
        "year": "1973",
        "place": "Santiago, Chile",
        "mission": "he tried to transform Chile through elections, nationalization, and social reform",
        "pressure": "economic crisis, strikes, political enemies, and military pressure closed around him",
        "turn": "the palace was bombed during a coup, and Allende died inside La Moneda",
        "aftermath": "the elected experiment ended in dictatorship and became a warning about power",
        "hook": "He entered power by ballot. He left it while bombs hit the presidential palace.",
    },
    {
        "slug": "cabral-1973-guinea-bissau",
        "short_title": "KILLED BEFORE VICTORY",
        "title": "The Liberation Leader Killed Before Victory",
        "figure": "Amilcar Cabral",
        "role": "a liberation strategist fighting Portuguese rule",
        "year": "1973",
        "place": "Conakry, Guinea",
        "mission": "he organized schools, politics, and guerrilla resistance before independence arrived",
        "pressure": "the struggle created enemies outside the movement and dangerous tension inside it",
        "turn": "he was assassinated months before independence became real",
        "aftermath": "the country moved toward freedom, but its main architect never saw the result",
        "hook": "He built a path to independence. Then he was killed right before the door opened.",
    },
    {
        "slug": "madero-1913-mexico",
        "short_title": "TRUSTED THE WRONG GENERAL",
        "title": "The President Betrayed By His General",
        "figure": "Francisco Madero",
        "role": "Mexico's reform president",
        "year": "1913",
        "place": "Mexico City, Mexico",
        "mission": "he challenged dictatorship and promised a more democratic Mexico",
        "pressure": "old elites, military factions, and foreign pressure made his presidency fragile",
        "turn": "General Victoriano Huerta betrayed him, seized power, and Madero was killed",
        "aftermath": "the betrayal pushed the Mexican Revolution into an even bloodier phase",
        "hook": "He trusted a general to protect the republic. That general helped destroy him.",
    },
    {
        "slug": "kimpa-vita-1706-kongo",
        "short_title": "THE PROPHET THEY FEARED",
        "title": "The Prophet Burned For Reuniting Kongo",
        "figure": "Kimpa Vita",
        "role": "a young religious leader in the Kingdom of Kongo",
        "year": "1706",
        "place": "Kongo",
        "mission": "she called for a divided kingdom to reunite around a powerful spiritual message",
        "pressure": "rival nobles and church authorities feared how quickly her movement spread",
        "turn": "she was condemned for heresy and executed by fire",
        "aftermath": "her story survived as a warning about who gets punished for uniting people",
        "hook": "A young woman tried to reunite a broken kingdom. The powerful treated that as a threat.",
    },
    {
        "slug": "mary-celeste-1872",
        "short_title": "THE EMPTY SHIP",
        "title": "The Ship Found Sailing With Nobody On Board",
        "figure": "the Mary Celeste",
        "role": "a merchant ship crossing the Atlantic",
        "year": "1872",
        "place": "the Atlantic Ocean",
        "mission": "it was supposed to carry cargo safely across the sea",
        "pressure": "when another crew found it drifting, food, cargo, and personal items were still there",
        "turn": "the lifeboat was gone, but the people were never found",
        "aftermath": "the missing crew turned a normal voyage into one of the ocean's strangest mysteries",
        "hook": "A ship was found moving across the ocean. Everything was there except the people.",
        "category": "historical mystery",
    },
    {
        "slug": "dyatlov-pass-1959",
        "short_title": "THE TENT WAS CUT",
        "title": "The Hikers Who Fled Their Own Tent",
        "figure": "the Dyatlov Pass hikers",
        "role": "a student hiking group in the Ural Mountains",
        "year": "1959",
        "place": "the Ural Mountains",
        "mission": "they set out for a hard winter trek and expected to return as heroes",
        "pressure": "searchers later found their tent cut open from the inside",
        "turn": "the group had run into freezing darkness without proper gear",
        "aftermath": "every theory still has a missing piece, which is why the case never fully leaves people alone",
        "hook": "Nine hikers entered the mountains. Their tent was found cut open from the inside.",
        "category": "survival mystery",
    },
    {
        "slug": "flannan-isles-1900",
        "short_title": "THE EMPTY LIGHTHOUSE",
        "title": "The Lighthouse Keepers Who Vanished",
        "figure": "three Flannan Isles keepers",
        "role": "lighthouse keepers on a remote island",
        "year": "1900",
        "place": "the Flannan Isles",
        "mission": "they kept a lonely light burning for ships in dangerous water",
        "pressure": "a relief crew arrived to find the lighthouse empty, with no one answering",
        "turn": "the men were gone, and the island gave almost no clear explanation",
        "aftermath": "the missing keepers became a perfect ghost story because the silence did most of the work",
        "hook": "A ship came to replace three lighthouse keepers. The light was there. The men were not.",
        "category": "lost place mystery",
    },
    {
        "slug": "dancing-plague-1518",
        "short_title": "THE TOWN THAT DANCED",
        "title": "The Town That Could Not Stop Dancing",
        "figure": "the dancers of Strasbourg",
        "role": "ordinary townspeople caught in a strange outbreak",
        "year": "1518",
        "place": "Strasbourg",
        "mission": "one woman began dancing in the street, and nobody expected it to spread",
        "pressure": "more people joined until the town treated the dancing like a public crisis",
        "turn": "leaders tried to solve it by giving the dancers more space and music",
        "aftermath": "the event still feels unreal because fear, stress, and belief may have moved bodies like a command",
        "hook": "One woman started dancing in the street. Then an entire town could not look away.",
        "category": "strange true history",
    },
    {
        "slug": "bell-witch-1817",
        "short_title": "THE VOICE IN THE HOUSE",
        "title": "The Family Haunted By A Voice",
        "figure": "the Bell Witch legend",
        "role": "a Tennessee folklore story about a family and a voice",
        "year": "1817",
        "place": "Tennessee",
        "mission": "the family wanted a normal home life on the frontier",
        "pressure": "legend says knocks, whispers, and a strange voice began turning the house into a spectacle",
        "turn": "visitors came to hear it, and the story grew beyond the family itself",
        "aftermath": "whether you believe it or not, the legend survived because the scariest part was invisible",
        "hook": "A family said something was speaking inside their house. The voice became an American legend.",
        "category": "folklore horror",
    },
    {
        "slug": "tamam-shud-1948",
        "short_title": "THE CODE IN HIS POCKET",
        "title": "The Man Nobody Could Identify",
        "figure": "the Somerton Man",
        "role": "an unidentified man found near an Australian beach",
        "year": "1948",
        "place": "Adelaide, Australia",
        "mission": "he arrived with no clear identity, no obvious story, and no easy trail",
        "pressure": "investigators found a tiny scrap with the words Tamam Shud hidden in his clothing",
        "turn": "a rare book, possible code, and missing labels made the case feel designed to confuse people",
        "aftermath": "even with later clues, the mystery stayed famous because the setup sounded like fiction",
        "hook": "A man was found by the beach with a secret phrase hidden in his clothes.",
        "category": "historical mystery",
    },
]


@dataclass(frozen=True)
class StoryClipResult:
    output_dir: Path
    video_path: Path
    poster_path: Path
    metadata_path: Path
    segments_path: Path
    story_path: Path
    topic: dict[str, Any]


def english_story_mode_enabled(source_entry: dict[str, Any]) -> bool:
    if os.getenv("TIKTOK_EN_STORY_MODE", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    profile = str(source_entry.get("account_profile") or "").strip().lower()
    mode = str(source_entry.get("content_mode") or "").strip().lower()
    language = str(source_entry.get("audience_language") or "").strip().lower()
    return profile in {"future_en", "english", "en"} and language.startswith("en") and mode == "monetization"


def generate_tiktok_story_clip(
    root: Path,
    source_entry: dict[str, Any],
    *,
    sequence_index: int,
    logger: Callable[[str], None] | None = None,
) -> StoryClipResult:
    log = logger or (lambda message: None)
    story = build_story(source_entry, sequence_index=sequence_index, logger=log)
    source_id = str(source_entry.get("id") or "source")
    output_dir = root / "output" / "story_reels" / _safe_name(source_id) / f"{_timestamp()}-{sequence_index:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)

    story_path = output_dir / "story.json"
    script_path = output_dir / "script.txt"
    voiceover_path = output_dir / "voiceover.mp3"
    video_path = output_dir / f"story_{sequence_index:02d}_captioned.mp4"
    poster_path = output_dir / "poster.png"
    metadata_path = output_dir / "metadata.json"
    segments_path = output_dir / "segments.json"

    story_path.write_text(json.dumps(story, indent=2), encoding="utf-8")
    script_path.write_text(_script_text(story), encoding="utf-8")

    log(f"Generating original English story voiceover: {story['title']}.")
    _generate_openai_voiceover(story_narration_text(story), voiceover_path, logger=log)
    render_story_video(story, voiceover_path, video_path, poster_path, logger=log)

    segments = [
        {
            "start_seconds": 0.0,
            "end_seconds": round(_media_duration(video_path), 2),
            "excerpt": story["hook"],
            "reason": "Original English story reel generated from the Agent Proof Lab Shorts format.",
        }
    ]
    segments_path.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    metadata = {
        "render_version": RENDER_VERSION,
        "format": "tiktok_vertical_9_16",
        "source_url": str(source_entry.get("source_url") or ""),
        "source_id": source_id,
        "sequence_index": sequence_index,
        "title": story["title"],
        "short_title": story["short_title"],
        "topic_slug": story["slug"],
        "story_source": story.get("story_source") or "library",
        "category": story.get("category") or "",
        "caption_style": "lower_karaoke_phrase_captions_red_words",
        "poster_style": "thumbnail_safe_final_outro",
        "thumbnail_outro_seconds": THUMBNAIL_OUTRO_SECONDS,
        "duration_seconds": round(_media_duration(video_path), 2),
        "voiceover": str(voiceover_path),
        "video": str(video_path),
        "poster": str(poster_path),
        "created_at": _utc_now(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return StoryClipResult(
        output_dir=output_dir,
        video_path=video_path,
        poster_path=poster_path,
        metadata_path=metadata_path,
        segments_path=segments_path,
        story_path=story_path,
        topic=story,
    )


def build_story(
    source_entry: dict[str, Any],
    *,
    sequence_index: int,
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = logger or (lambda message: None)
    ai_story = _build_ai_story(source_entry, sequence_index=sequence_index, logger=log)
    if ai_story is not None:
        return ai_story
    return _build_library_story(source_entry, sequence_index=sequence_index)


def _build_library_story(source_entry: dict[str, Any], *, sequence_index: int) -> dict[str, Any]:
    topic = TOPIC_LIBRARY[(max(1, sequence_index) - 1) % len(TOPIC_LIBRARY)]
    beats = _beats_for_topic(topic)
    return {
        "slug": topic["slug"],
        "title": topic["title"],
        "short_title": topic["short_title"],
        "hook": topic["hook"],
        "category": topic.get("category") or "true history story",
        "source_url": str(source_entry.get("source_url") or ""),
        "story_source": "library",
        "beats": beats,
    }


def _build_ai_story(
    source_entry: dict[str, Any],
    *,
    sequence_index: int,
    logger: Callable[[str], None],
) -> dict[str, Any] | None:
    if os.getenv("TIKTOK_AI_STORY_DISCOVERY", "true").strip().lower() in AI_STORY_DISABLED_VALUES:
        return None
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None

    genre = GENRE_ROTATION[(max(1, sequence_index) - 1) % len(GENRE_ROTATION)]
    try:
        payload = _request_ai_story_payload(source_entry, sequence_index=sequence_index, genre=genre)
        story = _normalize_ai_story(payload, source_entry=source_entry, sequence_index=sequence_index, genre=genre)
    except Exception as exc:
        logger(f"AI story discovery failed, using fallback library: {exc}")
        return None

    logger(f"AI picked {story['category']}: {story['title']}.")
    return story


def _request_ai_story_payload(source_entry: dict[str, Any], *, sequence_index: int, genre: str) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI()
    model = os.getenv("OPENAI_STORY_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
    prompt = _ai_story_prompt(source_entry, sequence_index=sequence_index, genre=genre)
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create short, original, monetization-safe English TikTok story scripts. "
                    "Return strict JSON only. Avoid copyrighted fiction, explicit gore, current-news claims, "
                    "and unsupported accusations. For horror, frame the story as folklore, legend, or mystery."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.85,
    )
    content = str(completion.choices[0].message.content or "").strip()
    return _json_from_text(content)


def _ai_story_prompt(source_entry: dict[str, Any], *, sequence_index: int, genre: str) -> str:
    source_title = str(source_entry.get("title") or "").strip()
    source_hint = f"Current batch title: {source_title}." if source_title else "No user source was provided."
    previous_topics = ", ".join(topic["title"] for topic in TOPIC_LIBRARY[:8])
    return (
        f"Create one fresh vertical short story for English TikTok monetization.\n"
        f"Slot: {sequence_index}. Genre lane: {genre}. {source_hint}\n"
        "Rotate across true history, historical mysteries, eerie folklore, unsolved disappearances, strange events, "
        "survival stories, ancient mysteries, lost places, and dark biographies.\n"
        f"Do not reuse these fallback examples directly: {previous_topics}.\n"
        "Requirements:\n"
        "- 8 beats exactly.\n"
        "- Each beat narration is 16 to 27 spoken words, simple and punchy.\n"
        "- Total script should feel like a 60 to 75 second story.\n"
        "- Start with a curiosity hook, then setup, pressure, turn, consequence, final sting.\n"
        "- Use public-domain historical/folklore subject matter, no franchise characters, no graphic gore.\n"
        "- Onscreen text must be 2 to 5 words, bold, emotional, and safe for TikTok.\n"
        "Return JSON with keys: slug, title, short_title, hook, category, beats. "
        "beats is an array of objects with label, narration, onscreen_text."
    )


def _normalize_ai_story(
    payload: dict[str, Any],
    *,
    source_entry: dict[str, Any],
    sequence_index: int,
    genre: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("AI story payload was not a JSON object.")

    raw_beats = payload.get("beats")
    if not isinstance(raw_beats, list) or len(raw_beats) < 8:
        raise ValueError("AI story payload did not include 8 beats.")

    beats: list[dict[str, str]] = []
    for index, raw_beat in enumerate(raw_beats[:8], start=1):
        if not isinstance(raw_beat, dict):
            continue
        narration = _clean(raw_beat.get("narration"))
        onscreen = _clean(raw_beat.get("onscreen_text") or raw_beat.get("text"))
        label = _clean(raw_beat.get("label") or f"Beat {index}")
        if not narration or not onscreen:
            continue
        beats.append(
            {
                "label": _one_line(label, 28),
                "narration": narration,
                "onscreen_text": _one_line(onscreen.upper(), 28),
            }
        )
    if len(beats) < 8:
        raise ValueError("AI story beats were incomplete after normalization.")

    title = _clean(payload.get("title")) or f"English Story {sequence_index}"
    short_title = _clean(payload.get("short_title")) or title
    hook = _clean(payload.get("hook")) or beats[0]["narration"]
    category = _clean(payload.get("category")) or genre
    slug = _safe_name(_clean(payload.get("slug")) or f"{genre}-{sequence_index}").lower()
    return {
        "slug": slug,
        "title": _one_line(title, 72),
        "short_title": _one_line(short_title.upper(), 28),
        "hook": hook,
        "category": category,
        "source_url": str(source_entry.get("source_url") or ""),
        "story_source": "openai_story_discovery",
        "beats": beats,
    }


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("AI story response was not a JSON object.")
    return payload


def render_story_video(
    story: dict[str, Any],
    voiceover_path: Path,
    video_path: Path,
    poster_path: Path,
    *,
    logger: Callable[[str], None] | None = None,
) -> None:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed.")
    output_dir = video_path.parent
    segments_dir = output_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    beats = [beat for beat in story.get("beats") or [] if isinstance(beat, dict)]
    if not beats:
        raise RuntimeError("Story does not contain beats.")

    voice_duration = _media_duration(voiceover_path)
    story_duration = max(MIN_STORY_SECONDS, voice_duration)
    beat_durations = _beat_durations(beats, story_duration)
    segment_paths: list[Path] = []
    log = logger or (lambda message: None)

    _render_poster_frame(ffmpeg, story, beats[0], poster_path)
    for index, (beat, duration) in enumerate(zip(beats, beat_durations), start=1):
        segment_path = segments_dir / f"beat_{index:02d}.mp4"
        log(f"Rendering story beat {index}/{len(beats)}.")
        _render_beat_segment(ffmpeg, story, beat, index, len(beats), duration, segment_path)
        segment_paths.append(segment_path)

    outro_path = segments_dir / "beat_99_thumbnail_outro.mp4"
    _render_thumbnail_outro_segment(ffmpeg, poster_path, outro_path, THUMBNAIL_OUTRO_SECONDS)
    segment_paths.append(outro_path)
    concat_path = output_dir / "concat.txt"
    concat_path.write_text(_concat_file(segment_paths), encoding="utf-8")
    _merge_segments_with_audio(ffmpeg, concat_path, voiceover_path, video_path, story_duration + THUMBNAIL_OUTRO_SECONDS)


def story_narration_text(story: dict[str, Any]) -> str:
    return " ".join(str(beat.get("narration") or "").strip() for beat in story.get("beats") or []).strip()


def _generate_openai_voiceover(
    narration: str,
    output_path: Path,
    *,
    logger: Callable[[str], None],
) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for English story voiceover.")
    model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip() or "gpt-4o-mini-tts"
    voice = os.getenv("OPENAI_TTS_VOICE", "verse").strip() or "verse"
    fallback_model = os.getenv("OPENAI_TTS_FALLBACK_MODEL", "tts-1").strip() or "tts-1"
    fallback_voice = os.getenv("OPENAI_TTS_FALLBACK_VOICE", "alloy").strip() or "alloy"
    try:
        _openai_speech_to_file(model, voice, narration, output_path)
    except Exception as exc:
        if model == fallback_model and voice == fallback_voice:
            raise
        logger(f"OpenAI TTS {model}/{voice} failed, retrying {fallback_model}/{fallback_voice}: {exc}")
        _openai_speech_to_file(fallback_model, fallback_voice, narration, output_path)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("OpenAI voiceover did not produce an audio file.")


def _openai_speech_to_file(model: str, voice: str, narration: str, output_path: Path) -> None:
    from openai import OpenAI

    client = OpenAI()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    speech = client.audio.speech.create(
        model=model,
        voice=voice,
        input=narration,
        response_format="mp3",
    )
    if hasattr(speech, "write_to_file"):
        speech.write_to_file(output_path)
        return
    content = getattr(speech, "content", None)
    if isinstance(content, bytes):
        output_path.write_bytes(content)
        return
    data = speech.read() if hasattr(speech, "read") else bytes(speech)
    output_path.write_bytes(data)


def _beats_for_topic(topic: dict[str, str]) -> list[dict[str, str]]:
    figure = topic["figure"]
    return [
        {
            "label": "The Hook",
            "narration": topic["hook"],
            "onscreen_text": topic["short_title"],
        },
        {
            "label": "The Setup",
            "narration": f"In {topic['year']}, in {topic['place']}, {figure} stood in a country where power was already divided, nervous, and watching.",
            "onscreen_text": f"{topic['year']}. {topic['place'].split(',', 1)[0].upper()}",
        },
        {
            "label": "The Rise",
            "narration": f"He was {topic['role']}, and his promise was not small: {topic['mission']}.",
            "onscreen_text": "THE PROMISE WAS HUGE",
        },
        {
            "label": "The Threat",
            "narration": f"That promise sounded noble to supporters, but to people with money, weapons, or influence, it sounded dangerous.",
            "onscreen_text": "POWER GOT NERVOUS",
        },
        {
            "label": "The Pressure",
            "narration": f"The pressure grew because {topic['pressure']}. Every week, the room around him became smaller.",
            "onscreen_text": "THE ROOM GOT SMALLER",
        },
        {
            "label": "The Betrayal",
            "narration": f"Then the turn came: {topic['turn']}. The story stopped being reform, and became survival.",
            "onscreen_text": "THEN IT TURNED",
        },
        {
            "label": "The Aftermath",
            "narration": f"Afterward, {topic['aftermath']}. The result was bigger than one leader losing power.",
            "onscreen_text": "THE DAMAGE LASTED",
        },
        {
            "label": "The Name",
            "narration": f"That is why the name still matters: {figure}. A story about power, fear, and the cost of changing too much.",
            "onscreen_text": figure.upper(),
        },
    ]


def _render_beat_segment(
    ffmpeg: str,
    story: dict[str, Any],
    beat: dict[str, str],
    index: int,
    total: int,
    duration: float,
    output_path: Path,
) -> None:
    filters = ",".join(_beat_filters(story, beat, index=index, total=total, duration=duration))
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={BG}:s={WIDTH}x{HEIGHT}:d={max(0.5, duration):.3f}:r={FPS}",
            "-vf",
            filters,
            "-t",
            f"{max(0.5, duration):.3f}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            str(output_path),
        ],
        timeout=180,
    )


def _render_poster_frame(ffmpeg: str, story: dict[str, Any], beat: dict[str, str], output_path: Path) -> None:
    filters = ",".join(_poster_filters(story, beat))
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={BG}:s={WIDTH}x{HEIGHT}:d=1:r=1",
            "-vf",
            filters,
            "-frames:v",
            "1",
            str(output_path),
        ],
        timeout=60,
    )


def _render_thumbnail_outro_segment(ffmpeg: str, poster_path: Path, output_path: Path, duration: float) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{max(0.1, duration):.3f}",
            "-i",
            str(poster_path),
            "-vf",
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS}",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            str(output_path),
        ],
        timeout=60,
    )


def _merge_segments_with_audio(ffmpeg: str, concat_path: Path, voiceover_path: Path, output_path: Path, duration: float) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-i",
            str(voiceover_path),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "sine=frequency=82:sample_rate=48000",
            "-filter_complex",
            "[1:a]volume=1.0[a0];[2:a]volume=0.030[a1];[a0][a1]amix=inputs=2:duration=longest:dropout_transition=2[aout]",
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "160k",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        timeout=300,
    )


def _beat_filters(story: dict[str, Any], beat: dict[str, str], *, index: int, total: int, duration: float) -> list[str]:
    accent = [GREEN, CYAN, AMBER, RED][(index - 1) % 4]
    progress_height = int(1510 * (index / max(total, 1)))
    filters = [
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color={BG}:t=fill",
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color={_fallback_color(index)}@0.22:t=fill",
        f"drawbox=x='-260+mod(t*210+{index * 110}\\,1600)':y=0:w=180:h={HEIGHT}:color={AMBER}@0.080:t=fill",
        f"drawbox=x=54:y=78:w=972:h=1520:color={PANEL}@0.58:t=fill",
        f"drawbox=x=54:y=78:w=972:h=1520:color={accent}@0.42:t=4",
        f"drawbox=x=976:y=142:w=10:h=1510:color=white@0.13:t=fill",
        f"drawbox=x=976:y=142:w=10:h={progress_height}:color={accent}:t=fill",
        _drawtext("TRUE STORY", 70, 92, 34, "white@0.92", max_chars=14, borderw=4),
        _drawtext(_one_line(str(story.get("short_title") or ""), 21), 70, 138, 28, accent, max_chars=21, borderw=4),
        _drawtext(f"{index:02d}/{total:02d}", 860, 92, 34, "white@0.88", max_chars=5, borderw=4),
        f"drawbox=x=112:y=340:w=856:h=610:color={_fallback_color(index)}@0.42:t=fill",
        f"drawbox=x=112:y=340:w=856:h=610:color=black@0.18:t=fill",
        f"drawbox=x=148:y=386:w=784:h=72:color={accent}@0.18:t=fill",
        _drawtext(_one_line(beat.get("label", "STORY").upper(), 22), 172, 404, 34, accent, max_chars=22, borderw=5),
        f"drawbox=x='160+mod(t*80\\,700)':y=858:w=240:h=22:color=white@0.16:t=fill",
        f"drawbox=x=218:y=520:w=644:h=210:color=white@0.055:t=fill",
        f"drawbox=x=264:y=572:w=552:h=98:color={accent}@0.16:t=fill",
        f"drawbox=x=324:y=456:w=432:h=346:color={accent}@0.10:t=fill",
        f"drawbox=x=464:y=414:w=152:h=432:color={accent}@0.64:t=fill",
    ]
    y = 1006
    for line in _wrap_text(str(beat.get("onscreen_text") or "").upper(), max_chars=18, max_lines=3):
        filters.append(_drawtext_center(line, y, 68, WHITE, max_chars=18, borderw=9))
        y += 82
    filters.extend(_karaoke_caption_filters(beat, duration=duration))
    return filters


def _poster_filters(story: dict[str, Any], beat: dict[str, str]) -> list[str]:
    lines = _headline_lines(str(story.get("short_title") or beat.get("onscreen_text") or story.get("title") or "TRUE STORY"))
    start_y = 690 - (len(lines) - 1) * 58
    filters = [
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color=0x121015:t=fill",
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color={RED}@0.16:t=fill",
        f"drawbox=x=70:y=270:w=940:h=1130:color={AMBER}@0.10:t=fill",
        f"drawbox=x=0:y=0:w={WIDTH}:h=190:color=black@0.38:t=fill",
        f"drawbox=x=0:y=1510:w={WIDTH}:h=410:color=black@0.28:t=fill",
        f"drawbox=x=0:y=0:w=18:h={HEIGHT}:color={RED}@0.88:t=fill",
        f"drawbox=x={WIDTH - 18}:y=0:w=18:h={HEIGHT}:color={AMBER}@0.80:t=fill",
        _drawtext("TRUE HISTORY", 70, 64, 34, "white@0.94", max_chars=14, borderw=5),
        _drawtext("WATCH THE TURN", 676, 70, 28, "white@0.86", max_chars=16, borderw=4),
    ]
    for line_index, line in enumerate(lines):
        y = start_y + line_index * 128
        box_color = f"{RED}@0.84" if line_index == len(lines) - 1 else "black@0.62"
        filters.append(f"drawbox=x=72:y={y - 16}:w=936:h=108:color={box_color}:t=fill")
        filters.append(_drawtext_center(line, y, 76, WHITE, max_chars=18, borderw=10))
    filters.append(_drawtext_center("FULL STORY IN 60 SECONDS", start_y + len(lines) * 128 + 46, 34, "white@0.90", max_chars=26, borderw=4))
    return filters


def _karaoke_caption_filters(beat: dict[str, str], *, duration: float) -> list[str]:
    text = str(beat.get("narration") or "")
    groups = _caption_word_groups(text.upper(), max_words=4)
    flat_words = [word for group in groups for word in group]
    timing_windows = _word_timing_windows(flat_words, duration)
    filters: list[str] = []
    word_cursor = 0
    for group in groups:
        group_windows = timing_windows[word_cursor : word_cursor + len(group)]
        word_cursor += len(group)
        if not group_windows:
            continue
        group_start = max(0.0, group_windows[0][0])
        group_end = min(duration, group_windows[-1][1])
        group_enable = _between(group_start, group_end)
        lines = _caption_group_lines(group, size=74)
        y_start, size, line_gap = _caption_group_layout(len(lines))
        lines = _caption_group_lines(group, size=size)
        group_window_index = 0
        for line_index, line_words in enumerate(lines):
            y = y_start + line_index * line_gap
            line_text = " ".join(line_words)
            line_width = _text_width(line_text, size)
            x = max(32, int((WIDTH - min(line_width, 1016)) / 2))
            filters.append(_drawtext(line_text, x, y, size, WHITE, max_chars=120, borderw=10, enable=group_enable))
            for word_index, word in enumerate(line_words):
                start, end = group_windows[group_window_index] if group_window_index < len(group_windows) else (0.0, duration)
                prefix = " ".join(line_words[:word_index])
                word_x = x + (_text_width(f"{prefix} ", size) if prefix else 0)
                filters.append(_drawtext(_one_line(word, 20), word_x, y, size, RED, max_chars=20, borderw=10, enable=_between(start, end)))
                group_window_index += 1
    return filters


def _beat_durations(beats: list[dict[str, str]], duration: float) -> list[float]:
    weights = [max(1.0, math.sqrt(len(str(beat.get("narration") or "")))) for beat in beats]
    total = sum(weights) or 1.0
    return [max(4.0, duration * (weight / total)) for weight in weights]


def _caption_word_groups(text: str, *, max_words: int) -> list[list[str]]:
    words = [word for word in re.split(r"\s+", _clean(text)) if word]
    if not words:
        return [["STORY"]]
    groups: list[list[str]] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        ends_phrase = word.rstrip().endswith((",", ";", ":", ".", "!", "?"))
        if len(current) >= max_words or (ends_phrase and len(current) >= 3):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _caption_group_lines(words: list[str], *, size: int) -> list[list[str]]:
    if not words:
        return [["STORY"]]
    text = " ".join(words)
    if len(words) <= 1 or _text_width(text, size) <= 996:
        return [words]
    best_split = 1
    best_width = WIDTH
    for split in range(1, len(words)):
        widest = max(_text_width(" ".join(words[:split]), size), _text_width(" ".join(words[split:]), size))
        if widest < best_width:
            best_width = widest
            best_split = split
    return [words[:best_split], words[best_split:]]


def _caption_group_layout(line_count: int) -> tuple[int, int, int]:
    if line_count <= 1:
        return 1312, 76, 88
    return 1236, 68, 84


def _word_timing_windows(words: list[str], duration: float) -> list[tuple[float, float]]:
    if not words:
        return [(0.0, duration)]
    weights = [_word_timing_weight(word) for word in words]
    total = sum(weights) or 1.0
    cursor = 0.0
    windows: list[tuple[float, float]] = []
    for index, weight in enumerate(weights):
        word_duration = duration * (weight / total)
        start = min(duration, cursor)
        end = duration if index == len(words) - 1 else min(duration, cursor + word_duration)
        windows.append((start, end))
        cursor = end
    return windows


def _word_timing_weight(word: str) -> float:
    letters = re.sub(r"[^A-Za-z0-9]", "", word)
    return max(0.7, min(3.2, len(letters) ** 0.72))


def _between(start: float, end: float) -> str:
    return f"between(t\\,{start:.2f}\\,{end:.2f})"


def _drawtext(
    text: str,
    x: int,
    y: int,
    size: int,
    color: str,
    *,
    max_chars: int = 70,
    borderw: int = 0,
    enable: str = "",
) -> str:
    font = _font_path()
    escaped = _escape_drawtext(_one_line(text, max_chars))
    font_part = f"fontfile={font}:" if font else ""
    border_part = f":borderw={borderw}:bordercolor=black" if borderw else ""
    enable_part = f":enable='{enable}'" if enable else ""
    return f"drawtext={font_part}text='{escaped}':fontcolor={color}:fontsize={size}:x={x}:y={y}{border_part}{enable_part}"


def _drawtext_center(text: str, y: int, size: int, color: str, *, max_chars: int = 70, borderw: int = 0) -> str:
    font = _font_path()
    escaped = _escape_drawtext(_one_line(text, max_chars))
    font_part = f"fontfile={font}:" if font else ""
    border_part = f":borderw={borderw}:bordercolor=black" if borderw else ""
    return f"drawtext={font_part}text='{escaped}':fontcolor={color}:fontsize={size}:x=(w-text_w)/2:y={y}{border_part}"


def _text_width(text: str, size: int) -> int:
    return int(sum(0.36 * size if char == " " else 0.66 * size for char in text))


def _headline_lines(text: str) -> list[str]:
    return [_one_line(line, 18).upper() for line in _wrap_text(text.upper(), max_chars=18, max_lines=3)]


def _wrap_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    words = []
    for raw_word in re.split(r"\s+", text.strip()):
        words.extend(_word_chunks(raw_word, max_chars))
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines or [text[:max_chars]]


def _word_chunks(word: str, max_chars: int) -> list[str]:
    if len(word) <= max_chars:
        return [word]
    chunks = []
    remaining = word
    while len(remaining) > max_chars:
        chunks.append(remaining[: max_chars - 1] + "-")
        remaining = remaining[max_chars - 1 :]
    if remaining:
        chunks.append(remaining)
    return chunks


def _one_line(text: str, max_chars: int) -> str:
    compact = _clean(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "..."


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _fallback_color(index: int) -> str:
    return ["0x3b244a", "0x20445f", "0x6b2d2d", "0x5f4a20"][(index - 1) % 4]


def _font_path() -> str:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate.replace("\\", "/").replace(":", "\\\\:")
    return ""


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    return ""


def _ffprobe() -> str:
    return shutil.which("ffprobe") or ""


def _media_duration(path: Path) -> float:
    ffprobe = _ffprobe()
    if not ffprobe or not path.exists():
        return MIN_STORY_SECONDS
    completed = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=30,
    )
    try:
        return max(0.1, float((completed.stdout or "").strip() or "0"))
    except ValueError:
        return MIN_STORY_SECONDS


def _concat_file(paths: list[Path]) -> str:
    return "".join(
        f"file '{str(path.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
        for path in paths
    )


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-8:])
        raise RuntimeError(tail or str(exc)) from exc


def _script_text(story: dict[str, Any]) -> str:
    lines = [story["title"], "", story["hook"], ""]
    for index, beat in enumerate(story.get("beats") or [], start=1):
        lines.extend([f"{index}. {beat.get('label')}", str(beat.get("narration") or ""), ""])
    return "\n".join(lines).strip() + "\n"


def _safe_name(value: str) -> str:
    safe = "".join(char for char in value if char.isalnum() or char in {"-", "_"}).strip()
    return safe or "source"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

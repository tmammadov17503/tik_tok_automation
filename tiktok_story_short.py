from __future__ import annotations

import base64
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


WIDTH = 1080
HEIGHT = 1920
SCENE_WIDTH = 540
SCENE_HEIGHT = 960
FPS = 30
MIN_STORY_SECONDS = 64.0
THUMBNAIL_OUTRO_SECONDS = 0.65
RENDER_VERSION = "tiktok_story_reel_v6_safe_hooked_comic_panels"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_IMAGE_SIZE = "1024x1536"

BG = "0x070911"
PANEL = "0x101820"
GREEN = "0x27e46f"
CYAN = "0x39c5ff"
AMBER = "0xffc857"
RED = "0xff4d5e"
WHITE = "white"
MUTED = "0xd8dde5"
CAPTION_SAFE_LEFT = 56
CAPTION_SAFE_RIGHT = WIDTH - 176
CAPTION_SAFE_WIDTH = CAPTION_SAFE_RIGHT - CAPTION_SAFE_LEFT
CAPTION_MIN_FONT_SIZE = 44
CAPTION_WORD_GAP_RATIO = 0.30
POSTER_SAFE_TEXT_WIDTH = 860
POSTER_MIN_FONT_SIZE = 54

AI_STORY_DISABLED_VALUES = {"0", "false", "no", "off"}
GENRE_ROTATION = [
    "strange true history",
    "survival story",
    "forgotten historical betrayal",
    "lost place mystery",
    "folklore legend",
    "ancient mystery",
    "dark biography",
    "unbelievable true story",
    "historical mystery",
]
HOOK_OPENERS = [
    "Did you know this actually happened?",
    "Have you ever heard this story?",
    "What if I told you this was real?",
    "This sounds fake, but it happened.",
    "You probably never heard this part.",
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
            "reason": "Original English story reel generated for the autonomous story account.",
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
        "caption_style": "lower_story_karaoke_captions_red_words",
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
        return _with_opening_hook(ai_story, sequence_index=sequence_index)
    return _with_opening_hook(_build_library_story(source_entry, sequence_index=sequence_index), sequence_index=sequence_index)


def _build_library_story(source_entry: dict[str, Any], *, sequence_index: int) -> dict[str, Any]:
    lane_index = max(1, sequence_index) - 1
    lane = GENRE_ROTATION[lane_index % len(GENRE_ROTATION)]
    candidates = [topic for topic in TOPIC_LIBRARY if _topic_matches_lane(topic, lane)]
    if not candidates:
        candidates = list(TOPIC_LIBRARY)
    topic = candidates[lane_index % len(candidates)]
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


def _topic_matches_lane(topic: dict[str, str], lane: str) -> bool:
    category = str(topic.get("category") or "true history story").lower()
    title = str(topic.get("title") or "").lower()
    haystack = f"{category} {title} {topic.get('slug', '')}".lower()
    lane_lower = lane.lower()
    if "survival" in lane_lower:
        return "survival" in haystack or "disaster" in haystack
    if "lost place" in lane_lower:
        return "lost" in haystack or "lighthouse" in haystack
    if "folklore" in lane_lower:
        return "folklore" in haystack or "legend" in haystack or "horror" in haystack
    if "ancient" in lane_lower:
        return "ancient" in haystack or "prophet" in haystack or "kingdom" in haystack
    if "biography" in lane_lower:
        return "biography" in haystack or (not topic.get("category") and any(word in title for word in ("president", "prime minister", "leader")))
    if "unbelievable" in lane_lower:
        return "strange" in haystack or "dancing" in haystack or "could not stop" in haystack
    if "mystery" in lane_lower:
        return "mystery" in haystack or "vanished" in haystack or "nobody" in haystack
    if "history" in lane_lower or "betrayal" in lane_lower:
        return "history" in haystack or not topic.get("category")
    return True


def _build_ai_story(
    source_entry: dict[str, Any],
    *,
    sequence_index: int,
    logger: Callable[[str], None],
) -> dict[str, Any] | None:
    if os.getenv("TIKTOK_AI_STORY_DISCOVERY", "false").strip().lower() in AI_STORY_DISABLED_VALUES:
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
                    "Return strict JSON only. Use well-known public-domain history, documented mysteries, "
                    "or clearly framed folklore. Avoid copyrighted fiction, explicit gore, current-news claims, "
                    "unsupported accusations, and invented historical events."
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
        "- First beat narration must begin with a curiosity hook like: Did you know this actually happened? / Have you ever heard this story? / What if I told you this was real?\n"
        "- Then continue with setup, pressure, turn, consequence, final sting.\n"
        "- Use a real, widely known historical/folklore subject. Do not invent disasters, causes, dates, or places.\n"
        "- If the story is folklore or horror, clearly frame it as legend, rumor, or alleged haunting.\n"
        "- No franchise characters, no graphic gore, no modern crime allegations.\n"
        "- Onscreen text must be 2 to 5 words, bold, emotional, and safe for TikTok.\n"
        "- Each beat must include visual: one concrete comic-panel scene description, with setting, character/action, props, and mood.\n"
        "- Each beat may include palette: 3 to 5 color/mood words.\n"
        "Return JSON with keys: slug, title, short_title, hook, category, beats. "
        "beats is an array of objects with label, narration, onscreen_text, visual, palette."
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
                "visual": _one_line(_clean(raw_beat.get("visual")), 240),
                "palette": _one_line(_clean(raw_beat.get("palette")), 90),
                "motion": _one_line(_clean(raw_beat.get("motion")) or "slow push with subtle parallax", 80),
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
        "beats": [_with_fallback_visual_for_story(beat, index, title=title, category=category) for index, beat in enumerate(beats, start=1)],
    }


def _with_fallback_visual_for_story(beat: dict[str, str], index: int, *, title: str, category: str) -> dict[str, str]:
    enriched = dict(beat)
    if not enriched.get("visual"):
        enriched["visual"] = (
            f"comic-book scene for {title}, beat {index}: {enriched.get('label')}. "
            f"Show the exact story moment from the narration with expressive characters, props, and historical setting."
        )
    if not enriched.get("palette"):
        enriched["palette"] = "dramatic comic colors, ink shadows, cinematic highlights"
    if not enriched.get("motion"):
        enriched["motion"] = "slow push with subtle parallax"
    if category:
        enriched["visual"] = f"{enriched['visual']} Category mood: {category}."
    return enriched


def _with_opening_hook(story: dict[str, Any], *, sequence_index: int) -> dict[str, Any]:
    beats = [dict(beat) for beat in story.get("beats") or [] if isinstance(beat, dict)]
    if not beats:
        return story
    first = dict(beats[0])
    narration = _clean(first.get("narration"))
    if narration and not _starts_with_curiosity_hook(narration):
        opener = HOOK_OPENERS[(max(1, sequence_index) - 1) % len(HOOK_OPENERS)]
        first["narration"] = f"{opener} {narration}"
        first["label"] = _clean(first.get("label") or "The Hook") or "The Hook"
    beats[0] = first
    enriched = dict(story)
    enriched["beats"] = beats
    return enriched


def _starts_with_curiosity_hook(text: str) -> bool:
    lowered = text.strip().lower()
    starters = (
        "did you know",
        "have you ever",
        "have you heard",
        "what if i told you",
        "this sounds fake",
        "you probably never",
    )
    return any(lowered.startswith(starter) for starter in starters)


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
    scene_paths = _prepare_story_scene_images(story, beats, output_dir, logger=log)

    poster_background_path = segments_dir / "poster_background.ppm"
    if scene_paths.get(1):
        poster_background_path = scene_paths[1]
    else:
        _write_scene_background(story, beats[0], 0, len(beats), poster_background_path)
    _render_poster_frame(ffmpeg, story, beats[0], poster_path, poster_background_path)
    for index, (beat, duration) in enumerate(zip(beats, beat_durations), start=1):
        segment_path = segments_dir / f"beat_{index:02d}.mp4"
        background_path = scene_paths.get(index) or (segments_dir / f"background_{index:02d}.ppm")
        if not background_path.exists():
            _write_scene_background(story, beat, index, len(beats), background_path)
        log(f"Rendering story beat {index}/{len(beats)}.")
        _render_beat_segment(ffmpeg, story, beat, index, len(beats), duration, segment_path, background_path)
        segment_paths.append(segment_path)

    outro_path = segments_dir / "beat_99_thumbnail_outro.mp4"
    thumbnail_outro_seconds = min(0.95, max(0.2, THUMBNAIL_OUTRO_SECONDS))
    _render_thumbnail_outro_segment(ffmpeg, poster_path, outro_path, thumbnail_outro_seconds)
    segment_paths.append(outro_path)
    concat_path = output_dir / "concat.txt"
    concat_path.write_text(_concat_file(segment_paths), encoding="utf-8")
    _merge_segments_with_audio(ffmpeg, concat_path, voiceover_path, video_path, story_duration + thumbnail_outro_seconds)


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
    beats = [
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
    return [_with_default_visual(topic, beat, index) for index, beat in enumerate(beats, start=1)]


def _with_default_visual(topic: dict[str, str], beat: dict[str, str], index: int) -> dict[str, str]:
    enriched = dict(beat)
    figure = topic.get("figure", "the central character")
    place = topic.get("place", "a historical setting")
    year = topic.get("year", "the era")
    visual_templates = [
        f"dramatic opening portrait of {figure} in {place}, {year}, surrounded by symbolic clues from the story",
        f"wide establishing scene of {place} in {year}, architecture, weather, and tense atmosphere",
        f"{figure} making an important choice, papers, maps, witnesses, and period objects around them",
        "powerful opponents watching from shadows, official rooms, documents, guards, and pressure closing in",
        "the conflict escalating, dramatic lighting, worried faces, symbolic evidence, and a sense of danger",
        "the betrayal or turning point moment, cinematic composition, urgent movement, no gore",
        "aftermath scene showing consequences, empty rooms, broken symbols, people reacting in silence",
        f"final memorable portrait of {figure}, historical props, dramatic light, unresolved mood",
    ]
    enriched.setdefault("visual", visual_templates[(index - 1) % len(visual_templates)])
    enriched.setdefault("palette", _default_visual_palette(topic, index))
    enriched.setdefault("motion", "slow push with subtle parallax")
    return enriched


def _default_visual_palette(topic: dict[str, str], index: int) -> str:
    category = str(topic.get("category") or "").lower()
    if "horror" in category or "folklore" in category:
        return "dark forest green, candle amber, black shadows"
    if "mystery" in category or "lost" in category:
        return "midnight blue, fog gray, cold cyan, amber clue light"
    if "survival" in category:
        return "icy blue, storm gray, harsh white, danger red"
    return ["deep emerald, warm gold, red warning accents", "ink black, aged paper, muted teal"][(index - 1) % 2]


def _render_beat_segment(
    ffmpeg: str,
    story: dict[str, Any],
    beat: dict[str, str],
    index: int,
    total: int,
    duration: float,
    output_path: Path,
    background_path: Path,
) -> None:
    filters = ",".join(_beat_filters(story, beat, index=index, total=total, duration=duration))
    _run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(background_path),
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


def _render_poster_frame(
    ffmpeg: str,
    story: dict[str, Any],
    beat: dict[str, str],
    output_path: Path,
    background_path: Path,
) -> None:
    filters = ",".join(_poster_filters(story, beat))
    _run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(background_path),
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


def _prepare_story_scene_images(
    story: dict[str, Any],
    beats: list[dict[str, Any]],
    output_dir: Path,
    *,
    logger: Callable[[str], None],
) -> dict[int, Path]:
    scene_root = output_dir / "scenes"
    scene_root.mkdir(parents=True, exist_ok=True)
    manifest_path = scene_root / "visual_manifest.json"
    required = _story_images_required()
    enabled = os.getenv("TIKTOK_GENERATE_AI_STORY_IMAGES", "true").strip().lower() not in AI_STORY_DISABLED_VALUES
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
    quality = os.getenv("OPENAI_IMAGE_QUALITY", "low").strip() or "low"
    scene_paths: dict[int, Path] = {}
    errors: list[str] = []

    if not enabled or not api_key or not model:
        message = "OpenAI story image generation is not configured."
        if required:
            raise RuntimeError(f"{message} Refusing to send fallback-looking English story video.")
        errors.append(message)
        _write_visual_manifest(manifest_path, model="", quality="", scene_paths=scene_paths, errors=errors)
        return scene_paths

    for index, beat in enumerate(beats, start=1):
        scene_path = scene_root / f"scene_{index:02d}.png"
        if scene_path.exists() and scene_path.stat().st_size > 0:
            scene_paths[index] = scene_path
            continue
        prompt = _openai_scene_prompt(story, beat, index=index)
        try:
            logger(f"Generating comic story panel {index}/{len(beats)}.")
            _generate_openai_scene_image(api_key=api_key, model=model, quality=quality, prompt=prompt, output_path=scene_path)
            scene_paths[index] = scene_path
        except Exception as exc:
            errors.append(f"scene_{index:02d}: {_safe_visual_error(exc)}")
            if required:
                raise RuntimeError(
                    "OpenAI story image generation failed; refusing to send fallback-looking English story video: "
                    f"{_safe_visual_error(exc)}"
                ) from exc

    _write_visual_manifest(manifest_path, model=model, quality=quality, scene_paths=scene_paths, errors=errors)
    return scene_paths


def _story_images_required() -> bool:
    value = os.getenv("TIKTOK_REQUIRE_AI_STORY_IMAGES", "true").strip().lower()
    return value not in AI_STORY_DISABLED_VALUES


def _generate_openai_scene_image(
    *,
    api_key: str,
    model: str,
    quality: str,
    prompt: str,
    output_path: Path,
) -> None:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": DEFAULT_IMAGE_SIZE,
        "quality": quality,
        "n": 1,
    }
    request = Request(
        OPENAI_IMAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=240) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI image API error {exc.code}: {_safe_openai_error(body)}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI image network error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw or "{}")
        first = parsed["data"][0]
        if first.get("b64_json"):
            image_bytes = base64.b64decode(str(first["b64_json"]))
        elif first.get("url"):
            with urlopen(str(first["url"]), timeout=180) as image_response:
                image_bytes = image_response.read()
        else:
            raise KeyError("missing image data")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("OpenAI image API returned an invalid response.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    if output_path.stat().st_size <= 0:
        raise RuntimeError("OpenAI image API returned an empty image.")


def _openai_scene_prompt(story: dict[str, Any], beat: dict[str, Any], *, index: int) -> str:
    brand_style = os.getenv("TIKTOK_STORY_IMAGE_STYLE", "").strip()
    style = brand_style or (
        "vertical 9:16 high-detail comic-book historical illustration, thick clean black ink outlines, "
        "flat cinematic colors, dramatic shadows, expressive non-photorealistic characters, rich background detail, "
        "TikTok-ready composition with clear subject in the center and room for captions in the lower third"
    )
    return (
        f"{style}. "
        "No text, no captions, no logos, no watermarks, no readable documents, no speech bubbles, no UI. "
        "Avoid gore and graphic violence. Do not create a photorealistic exact likeness of a real person; "
        "use an original comic-inspired historical character design. "
        f"Series/story title: {story.get('short_title') or story.get('title')}. "
        f"Category: {story.get('category') or 'historical story'}. "
        f"Beat {index}: {beat.get('label')}. "
        f"Scene: {beat.get('visual') or beat.get('narration')}. "
        f"On-screen idea, not rendered as text: {beat.get('onscreen_text')}. "
        f"Mood and palette: {beat.get('palette') or 'dramatic historical comic, deep shadows, red and gold accents'}."
    )


def _write_visual_manifest(
    manifest_path: Path,
    *,
    model: str,
    quality: str,
    scene_paths: dict[int, Path],
    errors: list[str],
) -> None:
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "provider": "openai_images" if model else "fallback",
                "model": model,
                "quality": quality,
                "image_size": DEFAULT_IMAGE_SIZE,
                "render_version": RENDER_VERSION,
                "scene_count": len(scene_paths),
                "errors": errors,
                "scenes": {str(index): str(path) for index, path in sorted(scene_paths.items())},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _safe_openai_error(body: str) -> str:
    try:
        parsed = json.loads(body or "{}")
        message = parsed.get("error", {}).get("message") or parsed.get("message") or body
        return str(message)[:500]
    except ValueError:
        return body[:500]


def _safe_visual_error(exc: Exception) -> str:
    return re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", str(exc))[:500]


Color = tuple[int, int, int]


def _write_scene_background(
    story: dict[str, Any],
    beat: dict[str, str],
    index: int,
    total: int,
    output_path: Path,
) -> None:
    scene = _scene_key(story, beat)
    canvas = _new_gradient((7, 10, 20), (18, 21, 34))
    if scene == "ocean":
        _draw_ocean_ship_scene(canvas, index)
    elif scene == "mountain":
        _draw_mountain_scene(canvas, index)
    elif scene == "lighthouse":
        _draw_lighthouse_scene(canvas, index)
    elif scene == "haunted":
        _draw_haunted_scene(canvas, index)
    elif scene == "beach":
        _draw_beach_scene(canvas, index)
    elif scene == "town":
        _draw_town_scene(canvas, index)
    else:
        _draw_history_scene(canvas, index, total)
    _draw_vignette(canvas)
    _write_ppm(canvas, output_path)


def _scene_key(story: dict[str, Any], beat: dict[str, str]) -> str:
    slug = str(story.get("slug") or "").lower()
    title = str(story.get("title") or story.get("short_title") or "").lower()
    category = str(story.get("category") or "").lower()
    joined = " ".join([slug, title, category, str(beat.get("narration") or "").lower()])
    if any(token in joined for token in ("mary-celeste", "empty ship", "ship", "ocean", "atlantic")):
        return "ocean"
    if any(token in joined for token in ("dyatlov", "mountain", "hikers", "snow", "ural")):
        return "mountain"
    if any(token in joined for token in ("flannan", "lighthouse", "isles")):
        return "lighthouse"
    if any(token in joined for token in ("bell-witch", "witch", "haunted", "house", "voice")):
        return "haunted"
    if any(token in joined for token in ("tamam", "somerton", "beach", "adelaide", "code")):
        return "beach"
    if any(token in joined for token in ("dancing", "strasbourg", "town")):
        return "town"
    return "history"


def _new_gradient(top: Color, bottom: Color) -> bytearray:
    canvas = bytearray(SCENE_WIDTH * SCENE_HEIGHT * 3)
    for y in range(SCENE_HEIGHT):
        ratio = y / max(1, SCENE_HEIGHT - 1)
        color = (
            int(top[0] * (1 - ratio) + bottom[0] * ratio),
            int(top[1] * (1 - ratio) + bottom[1] * ratio),
            int(top[2] * (1 - ratio) + bottom[2] * ratio),
        )
        row = y * SCENE_WIDTH * 3
        for x in range(SCENE_WIDTH):
            offset = row + x * 3
            canvas[offset : offset + 3] = bytes(color)
    return canvas


def _write_ppm(canvas: bytearray, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(f"P6\n{SCENE_WIDTH} {SCENE_HEIGHT}\n255\n".encode("ascii") + bytes(canvas))


def _draw_ocean_ship_scene(canvas: bytearray, index: int) -> None:
    _draw_rect(canvas, 0, 515, SCENE_WIDTH, 445, (8, 31, 48), 1.0)
    _draw_circle(canvas, 430, 142, 52, (222, 228, 209), 0.95)
    _draw_circle(canvas, 406, 130, 54, (7, 10, 20), 0.72)
    for y in range(560, 930, 48):
        _draw_line(canvas, 20, y + (index * 9) % 34, 520, y - 18 + (index * 7) % 28, (56, 141, 160), 3, 0.45)
    _draw_polygon(canvas, [(112, 566), (410, 566), (360, 642), (160, 642)], (22, 20, 18), 1.0)
    _draw_polygon(canvas, [(152, 560), (246, 360), (246, 560)], (205, 210, 193), 0.88)
    _draw_polygon(canvas, [(255, 560), (352, 380), (352, 560)], (191, 197, 185), 0.78)
    _draw_line(canvas, 247, 336, 247, 586, (24, 22, 20), 5, 1.0)
    _draw_line(canvas, 353, 356, 353, 582, (24, 22, 20), 5, 1.0)
    _draw_rect(canvas, 0, 470, SCENE_WIDTH, 110, (187, 205, 201), 0.08)
    _draw_rect(canvas, 0, 700, SCENE_WIDTH, 70, (255, 255, 255), 0.05)


def _draw_mountain_scene(canvas: bytearray, index: int) -> None:
    for x in range(38, 520, 90):
        _draw_circle(canvas, x, 92 + (x % 40), 2, (230, 238, 255), 0.85)
    _draw_polygon(canvas, [(0, 575), (142, 270), (292, 575)], (38, 55, 76), 1.0)
    _draw_polygon(canvas, [(150, 575), (310, 230), (540, 575)], (45, 62, 83), 1.0)
    _draw_polygon(canvas, [(142, 270), (92, 380), (194, 380)], (218, 226, 231), 0.92)
    _draw_polygon(canvas, [(310, 230), (245, 376), (384, 374)], (228, 234, 238), 0.90)
    _draw_rect(canvas, 0, 565, SCENE_WIDTH, 395, (202, 210, 213), 0.88)
    _draw_polygon(canvas, [(180, 676), (292, 596), (395, 676)], (144, 74, 48), 1.0)
    _draw_polygon(canvas, [(292, 596), (395, 676), (292, 676)], (181, 96, 54), 0.95)
    _draw_line(canvas, 292, 596, 292, 676, (56, 31, 25), 4, 1.0)
    for x in range(70, 500, 58):
        _draw_line(canvas, x, 734 + (index * 5 + x) % 18, x + 58, 720 + (x % 30), (246, 248, 250), 4, 0.65)


def _draw_lighthouse_scene(canvas: bytearray, index: int) -> None:
    _draw_rect(canvas, 0, 565, SCENE_WIDTH, 395, (5, 32, 45), 1.0)
    _draw_circle(canvas, 90, 126, 44, (220, 224, 205), 0.86)
    _draw_polygon(canvas, [(0, 618), (190, 510), (360, 617)], (31, 35, 39), 1.0)
    _draw_polygon(canvas, [(320, 620), (540, 522), (540, 620)], (26, 32, 36), 1.0)
    _draw_polygon(canvas, [(350, 228), (438, 228), (460, 625), (324, 625)], (209, 213, 205), 0.96)
    _draw_rect(canvas, 338, 315, 112, 38, (128, 35, 38), 0.95)
    _draw_rect(canvas, 332, 422, 122, 38, (128, 35, 38), 0.95)
    _draw_rect(canvas, 340, 186, 106, 46, (26, 30, 34), 1.0)
    _draw_circle(canvas, 392, 209, 18, (255, 232, 132), 0.95)
    _draw_polygon(canvas, [(392, 209), (0, 110 + (index % 3) * 16), (0, 238 + (index % 2) * 18)], (255, 232, 132), 0.20)
    for y in range(638, 925, 48):
        _draw_line(canvas, 0, y, 540, y - 34, (76, 137, 153), 4, 0.52)


def _draw_haunted_scene(canvas: bytearray, index: int) -> None:
    _draw_circle(canvas, 404, 134, 58, (224, 221, 190), 0.85)
    for x in range(0, 560, 55):
        _draw_rect(canvas, x, 430 - (x % 3) * 30, 18, 300, (8, 17, 20), 0.92)
        _draw_circle(canvas, x + 9, 405 - (x % 3) * 30, 52, (9, 22, 22), 0.78)
    _draw_polygon(canvas, [(116, 610), (270, 432), (424, 610)], (38, 30, 33), 1.0)
    _draw_rect(canvas, 150, 610, 240, 220, (46, 39, 40), 1.0)
    _draw_polygon(canvas, [(190, 548), (270, 470), (350, 548)], (35, 25, 29), 1.0)
    for x in (188, 318):
        _draw_rect(canvas, x, 654, 45, 58, (238, 179, 79), 0.82 if index % 2 else 0.60)
    _draw_rect(canvas, 254, 724, 48, 106, (15, 12, 13), 1.0)
    _draw_rect(canvas, 0, 790, SCENE_WIDTH, 170, (5, 10, 11), 0.95)


def _draw_beach_scene(canvas: bytearray, index: int) -> None:
    _draw_rect(canvas, 0, 465, SCENE_WIDTH, 180, (19, 82, 99), 1.0)
    _draw_rect(canvas, 0, 645, SCENE_WIDTH, 315, (155, 125, 82), 1.0)
    for y in range(484, 650, 32):
        _draw_line(canvas, 0, y + (index * 6) % 20, 540, y - 14, (121, 193, 196), 3, 0.48)
    _draw_polygon(canvas, [(348, 650), (415, 690), (390, 814), (316, 786)], (26, 29, 31), 1.0)
    _draw_circle(canvas, 375, 621, 30, (47, 38, 34), 1.0)
    _draw_polygon(canvas, [(118, 704), (250, 675), (278, 752), (140, 780)], (218, 203, 170), 1.0)
    _draw_line(canvas, 140, 725, 248, 704, (63, 56, 49), 2, 0.45)
    _draw_line(canvas, 150, 747, 248, 728, (63, 56, 49), 2, 0.35)
    _draw_rect(canvas, 0, 372, SCENE_WIDTH, 95, (245, 197, 117), 0.22)


def _draw_town_scene(canvas: bytearray, index: int) -> None:
    _draw_rect(canvas, 0, 578, SCENE_WIDTH, 382, (37, 28, 24), 1.0)
    for x, h in [(18, 250), (96, 315), (188, 270), (292, 338), (400, 284)]:
        _draw_rect(canvas, x, 578 - h, 80, h, (70, 49, 40), 1.0)
        _draw_polygon(canvas, [(x - 8, 578 - h), (x + 40, 528 - h), (x + 88, 578 - h)], (108, 45, 38), 1.0)
        _draw_rect(canvas, x + 22, 615 - h, 20, 28, (239, 172, 77), 0.82)
    for x in [120, 200, 280, 360, 440]:
        _draw_circle(canvas, x, 676 + (x + index * 9) % 24, 18, (26, 22, 20), 1.0)
        _draw_line(canvas, x, 695, x - 22, 765, (23, 20, 19), 5, 1.0)
        _draw_line(canvas, x, 716, x + 30, 754, (23, 20, 19), 4, 1.0)
        _draw_line(canvas, x - 6, 756, x - 34, 820, (23, 20, 19), 4, 1.0)
        _draw_line(canvas, x + 4, 756, x + 36, 820, (23, 20, 19), 4, 1.0)


def _draw_history_scene(canvas: bytearray, index: int, total: int) -> None:
    _draw_rect(canvas, 0, 610, SCENE_WIDTH, 350, (22, 20, 23), 1.0)
    _draw_polygon(canvas, [(88, 620), (270, 314), (456, 620)], (56, 48, 48), 0.72)
    _draw_circle(canvas, 270, 470, 72, (20, 18, 20), 1.0)
    _draw_rect(canvas, 220, 540, 100, 188, (17, 17, 20), 1.0)
    _draw_polygon(canvas, [(220, 548), (270, 512), (320, 548)], (27, 25, 27), 1.0)
    for x in (98, 406):
        _draw_rect(canvas, x, 380, 36, 342, (109, 86, 63), 0.82)
        _draw_rect(canvas, x - 12, 360, 60, 24, (138, 109, 75), 0.86)
    _draw_polygon(canvas, [(270, 180), (20, 534), (520, 534)], (226, 188, 83), 0.12)
    _draw_rect(canvas, 0, 0, SCENE_WIDTH, 120 + (index % max(1, total)) * 8, (102, 34, 38), 0.14)


def _draw_vignette(canvas: bytearray) -> None:
    cx = SCENE_WIDTH / 2
    cy = SCENE_HEIGHT / 2
    max_dist = math.hypot(cx, cy)
    for y in range(SCENE_HEIGHT):
        row = y * SCENE_WIDTH * 3
        for x in range(SCENE_WIDTH):
            dist = math.hypot(x - cx, y - cy) / max_dist
            alpha = max(0.0, min(0.58, (dist - 0.35) * 0.92))
            if alpha <= 0:
                continue
            offset = row + x * 3
            canvas[offset] = int(canvas[offset] * (1 - alpha))
            canvas[offset + 1] = int(canvas[offset + 1] * (1 - alpha))
            canvas[offset + 2] = int(canvas[offset + 2] * (1 - alpha))


def _draw_rect(canvas: bytearray, x: int, y: int, w: int, h: int, color: Color, alpha: float = 1.0) -> None:
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(SCENE_WIDTH, x + w)
    y2 = min(SCENE_HEIGHT, y + h)
    if x1 >= x2 or y1 >= y2:
        return
    for yy in range(y1, y2):
        row = yy * SCENE_WIDTH * 3
        for xx in range(x1, x2):
            _blend_pixel(canvas, row + xx * 3, color, alpha)


def _draw_circle(canvas: bytearray, cx: int, cy: int, radius: int, color: Color, alpha: float = 1.0) -> None:
    r2 = radius * radius
    for yy in range(max(0, cy - radius), min(SCENE_HEIGHT, cy + radius + 1)):
        row = yy * SCENE_WIDTH * 3
        dy2 = (yy - cy) * (yy - cy)
        for xx in range(max(0, cx - radius), min(SCENE_WIDTH, cx + radius + 1)):
            if (xx - cx) * (xx - cx) + dy2 <= r2:
                _blend_pixel(canvas, row + xx * 3, color, alpha)


def _draw_polygon(canvas: bytearray, points: list[tuple[int, int]], color: Color, alpha: float = 1.0) -> None:
    if len(points) < 3:
        return
    min_y = max(0, min(y for _, y in points))
    max_y = min(SCENE_HEIGHT - 1, max(y for _, y in points))
    for yy in range(min_y, max_y + 1):
        intersections: list[float] = []
        previous = points[-1]
        for current in points:
            x1, y1 = previous
            x2, y2 = current
            if (y1 <= yy < y2) or (y2 <= yy < y1):
                intersections.append(x1 + (yy - y1) * (x2 - x1) / (y2 - y1))
            previous = current
        intersections.sort()
        row = yy * SCENE_WIDTH * 3
        for left, right in zip(intersections[0::2], intersections[1::2]):
            x1 = max(0, int(math.ceil(left)))
            x2 = min(SCENE_WIDTH - 1, int(math.floor(right)))
            for xx in range(x1, x2 + 1):
                _blend_pixel(canvas, row + xx * 3, color, alpha)


def _draw_line(
    canvas: bytearray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Color,
    thickness: int = 1,
    alpha: float = 1.0,
) -> None:
    steps = max(abs(x2 - x1), abs(y2 - y1), 1)
    radius = max(0, thickness // 2)
    for step in range(steps + 1):
        ratio = step / steps
        x = int(round(x1 + (x2 - x1) * ratio))
        y = int(round(y1 + (y2 - y1) * ratio))
        _draw_rect(canvas, x - radius, y - radius, max(1, thickness), max(1, thickness), color, alpha)


def _blend_pixel(canvas: bytearray, offset: int, color: Color, alpha: float) -> None:
    if alpha >= 1:
        canvas[offset] = _clamp(color[0])
        canvas[offset + 1] = _clamp(color[1])
        canvas[offset + 2] = _clamp(color[2])
        return
    safe_alpha = max(0.0, min(1.0, alpha))
    inv = 1 - safe_alpha
    canvas[offset] = _clamp(canvas[offset] * inv + color[0] * safe_alpha)
    canvas[offset + 1] = _clamp(canvas[offset + 1] * inv + color[1] * safe_alpha)
    canvas[offset + 2] = _clamp(canvas[offset + 2] * inv + color[2] * safe_alpha)


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def _beat_filters(story: dict[str, Any], beat: dict[str, str], *, index: int, total: int, duration: float) -> list[str]:
    filters = [
        (
            "scale=1260:2240:force_original_aspect_ratio=increase,"
            f"rotate='0.005*sin(t*0.75+{index})':ow=iw:oh=ih:c=black@0,"
            f"crop={WIDTH}:{HEIGHT}:x='(iw-ow)/2+34*sin(t*0.48+{index})':"
            f"y='(ih-oh)/2+42*cos(t*0.36+{index})',"
            f"setsar=1,fps={FPS},eq=contrast=1.13:saturation=1.30:brightness=0.025,unsharp=5:5:0.55"
        ),
        _drawtext(_story_brand(), 742, 64, 24, "white@0.86", max_chars=20, borderw=3),
        _drawtext(
            _one_line(str(story.get("short_title") or ""), 34),
            56,
            64,
            24,
            "white@0.90",
            max_chars=34,
            borderw=3,
        ),
    ]
    filters.extend(_karaoke_caption_filters(beat, duration=duration, index=index))
    return filters


def _poster_filters(story: dict[str, Any], beat: dict[str, str]) -> list[str]:
    headline_source = story.get("hook_text") or story.get("short_title") or beat.get("onscreen_text") or story.get("hook")
    lines = _poster_headline_lines(str(headline_source or "STORY TIME"))
    line_count = max(1, len(lines))
    headline_size = _poster_headline_font_size(lines, 92 if line_count <= 2 else 82)
    line_gap = max(96, headline_size + 28)
    start_y = 1206 - int((line_count - 1) * line_gap * 0.64)
    panel_y = start_y - 54
    panel_h = line_count * line_gap + 70
    filters = [
        (
            "scale=1260:2240:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT}:x=(iw-ow)/2:y=(ih-oh)/2,"
            "setsar=1,eq=contrast=1.18:saturation=1.36:brightness=0.02,unsharp=5:5:0.60"
        ),
        f"drawbox=x=0:y=0:w={WIDTH}:h={HEIGHT}:color=black@0.12:t=fill",
        f"drawbox=x=0:y=0:w={WIDTH}:h=150:color=black@0.24:t=fill",
        f"drawbox=x=0:y=870:w={WIDTH}:h=520:color=black@0.28:t=fill",
        f"drawbox=x=0:y=1450:w={WIDTH}:h=470:color=black@0.22:t=fill",
        f"drawbox=x=0:y=0:w=16:h={HEIGHT}:color={RED}@0.82:t=fill",
        f"drawbox=x={WIDTH - 16}:y=0:w=16:h={HEIGHT}:color={AMBER}@0.74:t=fill",
        f"drawbox=x=76:y={panel_y}:w=928:h={panel_h}:color=black@0.48:t=fill",
        f"drawbox=x=98:y={panel_y + panel_h - 32}:w=884:h=16:color={RED}@0.92:t=fill",
        _drawtext(_story_brand(), 748, 58, 24, "white@0.86", max_chars=20, borderw=3),
    ]
    for line_index, line in enumerate(lines):
        y = start_y + line_index * line_gap
        if line_index == len(lines) - 1:
            underline_y = y + max(48, int(headline_size * 0.72))
            filters.append(f"drawbox=x=110:y={underline_y}:w=860:h=22:color={RED}@0.82:t=fill")
        filters.append(_drawtext_center(line, y, headline_size, WHITE, max_chars=18, borderw=11))
    filters.append(f"drawbox=x='10':y=0:w=150:h={HEIGHT}:color=white@0.08:t=fill")
    return filters


def _story_badge(story: dict[str, Any]) -> str:
    category = str(story.get("category") or "").upper()
    if "FOLKLORE" in category or "HORROR" in category or "LEGEND" in category:
        return "FOLKLORE STORY"
    if "MYSTERY" in category or "VANISH" in category or "LOST" in category:
        return "MYSTERY STORY"
    if "DISASTER" in category or "SURVIVAL" in category:
        return "SURVIVAL STORY"
    if "ANCIENT" in category:
        return "ANCIENT STORY"
    if "BIOGRAPHY" in category:
        return "DARK BIOGRAPHY"
    return "STORY TIME"


def _story_brand() -> str:
    return os.getenv("TIKTOK_STORY_BRAND", "DAMN WHAT A CLIP").strip().upper() or "DAMN WHAT A CLIP"


def _glitch_hook_filters() -> list[str]:
    return []


def _karaoke_caption_filters(beat: dict[str, str], *, duration: float, index: int) -> list[str]:
    text = str(beat.get("narration") or beat.get("onscreen_text") or beat.get("label") or "")
    groups = _caption_word_groups(text.upper(), max_words=3)
    flat_words = [word for group in groups for word in group]
    timing_windows = _word_timing_windows(flat_words, duration)
    filters: list[str] = []
    word_cursor = 0
    first_caption_y = 1304
    for group in groups:
        group_windows = timing_windows[word_cursor : word_cursor + len(group)]
        word_cursor += len(group)
        if not group_windows:
            continue
        group_start = max(0.0, group_windows[0][0])
        group_end = min(duration, group_windows[-1][1])
        group_enable = _between(group_start, group_end)
        draw_group = [_caption_display_word(word) for word in group]
        lines = _caption_group_lines(draw_group, size=72)
        y_start, size, line_gap = _caption_group_layout(len(lines))
        lines = _caption_group_lines(draw_group, size=size)
        size = _caption_fitted_font_size(lines, size)
        y_start, size, line_gap = _caption_group_layout(len(lines))
        line_gap = max(line_gap, size + 16)
        first_caption_y = min(first_caption_y, y_start)
        group_window_index = 0
        for line_index, line_words in enumerate(lines):
            y = y_start + line_index * line_gap
            line_width = _caption_line_width(line_words, size)
            x = _safe_caption_x(line_width)
            word_slots = _caption_line_slots(line_words, size)
            for word_index, word in enumerate(line_words):
                safe_word = _caption_display_word(word)
                start, end = group_windows[group_window_index] if group_window_index < len(group_windows) else (0.0, duration)
                word_x = x + word_slots[word_index][0]
                active_enable = _between(start, end)
                inactive_enable = f"{group_enable}*not({active_enable})"
                filters.append(_drawtext(safe_word, word_x, y, size, WHITE, max_chars=18, borderw=8, enable=inactive_enable))
                filters.append(_drawtext(safe_word, word_x, y, size, RED, max_chars=18, borderw=4, enable=active_enable))
                group_window_index += 1
    if index == 1:
        filters.append(_drawtext_center("REAL STORY", first_caption_y - 78, 32, "white@0.86", max_chars=16, borderw=4))
    return filters


def _beat_durations(beats: list[dict[str, str]], duration: float) -> list[float]:
    if not beats:
        return []
    safe_duration = max(0.1, duration)
    floor = min(2.8, safe_duration / len(beats))
    weights = [_beat_timing_weight(beat) for beat in beats]
    total = sum(weights) or 1.0
    remaining = max(0.0, safe_duration - floor * len(beats))
    durations = [floor + remaining * (weight / total) for weight in weights]
    durations[-1] += safe_duration - sum(durations)
    return [max(0.1, value) for value in durations]


def _beat_timing_weight(beat: dict[str, str]) -> float:
    text = str(beat.get("narration") or beat.get("onscreen_text") or beat.get("label") or "")
    words = [word for word in re.split(r"\s+", _clean(text)) if word]
    if not words:
        return 1.0
    return max(1.0, sum(_word_timing_weight(word) for word in words))


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
    remaining = [_caption_display_word(word) for word in words if _caption_display_word(word)]
    if not remaining:
        return [["STORY"]]
    lines: list[list[str]] = []
    while remaining:
        if len(remaining) == 1 or _caption_line_width(remaining, size) <= CAPTION_SAFE_WIDTH:
            lines.append(remaining)
            break
        split = _caption_split_index(remaining, size)
        lines.append(remaining[:split])
        remaining = remaining[split:]
    return lines


def _caption_split_index(words: list[str], size: int) -> int:
    best_split = 1
    best_score = float("inf")
    for split in range(1, len(words)):
        first_width = _caption_line_width(words[:split], size)
        second_width = _caption_line_width(words[split:], size)
        overflow = max(0, first_width - CAPTION_SAFE_WIDTH) + max(0, second_width - CAPTION_SAFE_WIDTH)
        balance = abs(first_width - second_width) * 0.15
        score = overflow * 10 + max(first_width, second_width) + balance
        if score < best_score:
            best_score = score
            best_split = split
    return best_split


def _caption_fitted_font_size(lines: list[list[str]], preferred_size: int) -> int:
    size = preferred_size
    while size > CAPTION_MIN_FONT_SIZE:
        if all(_caption_line_width(line, size) <= CAPTION_SAFE_WIDTH for line in lines):
            return size
        size -= 4
    return size


def _caption_display_word(word: str) -> str:
    return _one_line(_clean(word).replace("'", chr(8217)), 18)


def _safe_caption_x(line_width: int) -> int:
    if line_width >= CAPTION_SAFE_WIDTH:
        return CAPTION_SAFE_LEFT
    return CAPTION_SAFE_LEFT + int((CAPTION_SAFE_WIDTH - line_width) / 2)


def _caption_group_layout(line_count: int) -> tuple[int, int, int]:
    if line_count <= 1:
        return 1302, 64, 78
    if line_count == 2:
        return 1228, 58, 72
    return 1160, 52, 66


def _caption_line_width(words: list[str], size: int) -> int:
    slots = _caption_line_slots(words, size)
    if not slots:
        return 0
    last_x, last_width = slots[-1]
    return last_x + last_width


def _caption_line_slots(words: list[str], size: int) -> list[tuple[int, int]]:
    gap = max(12, int(size * CAPTION_WORD_GAP_RATIO))
    cursor = 0
    slots: list[tuple[int, int]] = []
    for word in words:
        width = _text_width(_caption_display_word(word), size)
        slots.append((cursor, width))
        cursor += width + gap
    return slots


def _word_timing_windows(words: list[str], duration: float) -> list[tuple[float, float]]:
    if not words:
        return [(0.0, duration)]
    weights = [_word_timing_weight(word) for word in words]
    total = sum(weights) or 1.0
    lead_in = min(0.18, max(0.0, duration * 0.025))
    tail_hold = min(0.24, max(0.0, duration * 0.035))
    spoken_duration = max(0.1, duration - lead_in - tail_hold)
    cursor = lead_in
    windows: list[tuple[float, float]] = []
    for index, weight in enumerate(weights):
        word_duration = spoken_duration * (weight / total)
        start = min(duration, cursor)
        end = min(duration, cursor + word_duration)
        windows.append((start, end))
        cursor = end
    return windows


def _word_timing_weight(word: str) -> float:
    letters = re.sub(r"[^A-Za-z0-9]", "", word)
    base = max(0.7, min(3.2, len(letters) ** 0.72))
    if re.search(r"[.!?…]$", word):
        base += 0.75
    elif re.search(r"[,;:]$", word):
        base += 0.35
    return base


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
    font_path = _font_path()
    if font_path:
        try:
            from PIL import ImageFont

            font = ImageFont.truetype(font_path.replace("\\:", ":"), size=size)
            return int(font.getlength(text))
        except Exception:
            pass
    return int(sum(0.36 * size if char == " " else 0.66 * size for char in text))


def _poster_headline_lines(text: str) -> list[str]:
    lines = _wrap_text(_clean(text).upper(), max_chars=18, max_lines=3)
    return [_one_line(line, 18).upper() for line in lines[:3]]


def _poster_headline_font_size(lines: list[str], preferred_size: int) -> int:
    size = preferred_size
    while size > POSTER_MIN_FONT_SIZE:
        if all(_text_width(line, size) <= POSTER_SAFE_TEXT_WIDTH for line in lines):
            return size
        size -= 4
    return size


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
    safe_text = text.replace("'", "\u2019")
    return safe_text.replace("\\", "\\\\").replace(":", "\\:").replace("%", "\\%")


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

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

from elevenlabs_budget import (
    BudgetDecision,
    commit_reservation,
    release_reservation,
    reserve_credits,
)


WIDTH = 1080
HEIGHT = 1920
SCENE_WIDTH = 540
SCENE_HEIGHT = 960
FPS = 30
MIN_STORY_SECONDS = 64.0
THUMBNAIL_OUTRO_SECONDS = 0.65
RENDER_VERSION = "tiktok_story_reel_v10_forced_alignment_atomic_retry"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_FORCED_ALIGNMENT_URL = "https://api.elevenlabs.io/v1/forced-alignment"
DEFAULT_IMAGE_SIZE = "1024x1536"
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

BG = "0x070911"
PANEL = "0x101820"
GREEN = "0x27e46f"
CYAN = "0x39c5ff"
AMBER = "0xffc857"
RED = "0xff4d5e"
WHITE = "white"
MUTED = "0xd8dde5"
CAPTION_SAFE_LEFT = 150
CAPTION_SAFE_RIGHT = WIDTH - 150
CAPTION_SAFE_WIDTH = CAPTION_SAFE_RIGHT - CAPTION_SAFE_LEFT
CAPTION_MAX_WORDS = 3
CAPTION_MIN_FONT_SIZE = 44
CAPTION_WORD_GAP_RATIO = 0.24
CAPTION_MIN_WORD_GAP = 20
CAPTION_SLOT_RIGHT_PAD = 2
CAPTION_OUTLINE_WIDTH = 5
CAPTION_ACTIVE_OUTLINE_WIDTH = 4
CAPTION_ASS_MARGIN = 120
CAPTION_ASS_MARGIN_V = 430
CAPTION_ASS_FONT_SIZE = 68
CAPTION_ASS_ACTIVE_COLOR = "&H5E4DFF&"
CAPTION_ASS_BASE_COLOR = "&HFFFFFF&"
POSTER_SAFE_TEXT_WIDTH = 860
POSTER_MIN_FONT_SIZE = 54

AI_STORY_DISABLED_VALUES = {"0", "false", "no", "off"}
GENRE_ROTATION = [
    "history shock",
    "mystery story",
    "lawsuit story",
    "court case",
    "reddit-style storytime",
    "cat animation",
    "world economy story",
    "2d animation moral story",
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
STORY_ROTATION_SUFFIX = re.compile(r"-r(?P<offset>\d+)$", re.IGNORECASE)
HOOK_OPENERS = [
    "Did you know this actually happened?",
    "Have you ever heard this story?",
    "What if I told you this was real?",
    "This sounds fake, but it happened.",
    "You probably never heard this part.",
]

TOPIC_LIBRARY: list[dict[str, Any]] = [
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
    {
        "slug": "hot-coffee-lawsuit-1994",
        "short_title": "THE LAWSUIT TWIST",
        "title": "The Hot Coffee Lawsuit People Remember Wrong",
        "hook": "Everyone jokes about this lawsuit, but the real courtroom details were much darker.",
        "category": "lawsuit story",
        "beats": [
            {
                "label": "The Hook",
                "narration": "Everyone jokes about this lawsuit, but the real courtroom details were much darker than the punchline people repeat.",
                "onscreen_text": "THE JOKE WAS WRONG",
                "visual": "1990s courtroom hallway, coffee cup evidence, newspaper headlines, serious jurors, dramatic comic style",
            },
            {
                "label": "The Spill",
                "narration": "A woman ordered coffee, the cup spilled, and the burns became severe enough to send the case into court.",
                "onscreen_text": "ONE CUP CHANGED EVERYTHING",
                "visual": "restaurant parking lot scene with spilled coffee, medical papers, shocked family, no graphic injury",
            },
            {
                "label": "The Evidence",
                "narration": "The argument was not just that coffee was hot. The argument was about temperature, warnings, and repeated complaints.",
                "onscreen_text": "THE DETAILS MATTERED",
                "visual": "lawyer presenting temperature chart and complaint files on a courtroom board",
            },
            {
                "label": "The Public",
                "narration": "Outside court, the story became a joke about greedy lawsuits before most people heard the evidence.",
                "onscreen_text": "THE STORY GOT FLIPPED",
                "visual": "television screens and newspaper cartoons turning a serious case into a public joke",
            },
            {
                "label": "The Verdict",
                "narration": "The jury heard the facts and made a decision that sounded shocking only after the context disappeared.",
                "onscreen_text": "CONTEXT DISAPPEARED",
                "visual": "jury box under warm courtroom lights, judge bench, evidence folders",
            },
            {
                "label": "The Lesson",
                "narration": "The case became famous because people remembered the headline, not the injuries, warnings, or legal reasoning.",
                "onscreen_text": "HEADLINES WON",
                "visual": "split scene: bold headline on one side, quiet court documents on the other",
            },
            {
                "label": "The Myth",
                "narration": "For years, it was used as proof that courts were ridiculous, even though the facts were more complicated.",
                "onscreen_text": "THE MYTH STUCK",
                "visual": "mythic oversized coffee cup casting shadow over court records",
            },
            {
                "label": "The Sting",
                "narration": "That is the strange part: one misunderstood lawsuit changed how millions of people talk about justice.",
                "onscreen_text": "JUSTICE BECAME A MEME",
                "visual": "final comic portrait of a courtroom turning into a viral media storm",
            },
        ],
    },
    {
        "slug": "miranda-rights-1966",
        "short_title": "THE WORDS POLICE SAY",
        "title": "The Court Case Behind The Words Police Say",
        "hook": "A criminal case changed the words millions of people now hear before questioning.",
        "category": "court case",
        "beats": [
            {
                "label": "The Hook",
                "narration": "A criminal case changed the words millions of people now hear before questioning begins.",
                "onscreen_text": "THE WARNING WAS BORN",
                "visual": "1960s police station corridor, interrogation room door, legal papers under harsh light",
            },
            {
                "label": "The Arrest",
                "narration": "The case started with an arrest, questioning, and a confession that later became the center of a constitutional fight.",
                "onscreen_text": "ONE CONFESSION MATTERED",
                "visual": "detectives at a desk, statement paper, clock on wall, restrained courtroom mood",
            },
            {
                "label": "The Question",
                "narration": "The question was simple but huge: did a suspect understand the right to stay silent and ask for counsel?",
                "onscreen_text": "DID HE KNOW?",
                "visual": "Supreme Court stairs, question mark made from law books, dramatic ink shading",
            },
            {
                "label": "The Court",
                "narration": "The Supreme Court said rights cannot be real if people are never clearly told they have them.",
                "onscreen_text": "RIGHTS NEED WORDS",
                "visual": "judges' bench, bright beam of light on the Bill of Rights",
            },
            {
                "label": "The Rule",
                "narration": "After that decision, warnings became part of police procedure across the United States.",
                "onscreen_text": "THE RULE SPREAD",
                "visual": "map of the United States with police notepads and court seals, no readable text",
            },
            {
                "label": "The Twist",
                "narration": "The original defendant's story did not become clean or heroic, but the legal rule became permanent.",
                "onscreen_text": "THE TWIST STAYED",
                "visual": "shadowed figure behind courthouse columns, legal rule glowing in foreground",
            },
            {
                "label": "The Culture",
                "narration": "Television turned the warning into a phrase almost everyone recognizes, even outside real courtrooms.",
                "onscreen_text": "TV MADE IT FAMOUS",
                "visual": "old television set showing police lights and a courtroom silhouette",
            },
            {
                "label": "The Sting",
                "narration": "That is why a single case still echoes every time someone says, you have the right to remain silent.",
                "onscreen_text": "ONE CASE ECHOES",
                "visual": "final cinematic courthouse at night, words represented as glowing abstract lines",
            },
        ],
    },
    {
        "slug": "tulip-mania-1637",
        "short_title": "THE FLOWER BUBBLE",
        "title": "The Flower Bubble That Looked Like Free Money",
        "hook": "A flower became so valuable that people started treating it like a fortune machine.",
        "category": "world economy story",
        "beats": [
            {
                "label": "The Hook",
                "narration": "A flower became so valuable that people started treating it like a fortune machine.",
                "onscreen_text": "A FLOWER GOT EXPENSIVE",
                "visual": "Dutch market in the 1600s, glowing tulip bulb on a table, merchants whispering",
            },
            {
                "label": "The Market",
                "narration": "In the Dutch Republic, rare tulip bulbs became status symbols, collectibles, and speculative bets.",
                "onscreen_text": "STATUS TURNED INTO MONEY",
                "visual": "busy canal market, tulip catalog, wealthy buyers studying rare bulbs",
            },
            {
                "label": "The Fever",
                "narration": "Prices rose because people believed someone else would always pay even more later.",
                "onscreen_text": "EVERYONE EXPECTED MORE",
                "visual": "price chart climbing like a vine, crowd reaching toward a tulip",
            },
            {
                "label": "The Contracts",
                "narration": "Some trades were not even for flowers in hand, but promises about future bulbs.",
                "onscreen_text": "PROMISES GOT TRADED",
                "visual": "contracts, wax seals, empty flower pot, nervous hands signing papers",
            },
            {
                "label": "The Break",
                "narration": "Then confidence cracked, buyers vanished, and prices stopped making sense in reverse.",
                "onscreen_text": "CONFIDENCE BROKE",
                "visual": "market stall suddenly empty, papers blowing through a canal street",
            },
            {
                "label": "The Lesson",
                "narration": "The story became a warning about bubbles, hype, and the dangerous phrase: this time is different.",
                "onscreen_text": "HYPE HAS A PRICE",
                "visual": "giant tulip shadow over modern stock chart silhouettes",
            },
            {
                "label": "The Echo",
                "narration": "Centuries later, people still compare new manias to those bulbs because the pattern feels familiar.",
                "onscreen_text": "THE PATTERN REPEATS",
                "visual": "tulip bulb beside coins, screens, and abstract market candles",
            },
            {
                "label": "The Sting",
                "narration": "The object changes, but the human dream stays the same: buy before everyone else believes.",
                "onscreen_text": "THE DREAM STAYS",
                "visual": "final surreal tulip blooming into coins and fading into smoke",
            },
        ],
    },
    {
        "slug": "forum-wallet-confession",
        "short_title": "THE WALLET TEST",
        "title": "The Online Confession About A Wallet",
        "hook": "This is an original forum-style story about one wallet and a decision that would not leave him alone.",
        "category": "reddit-style storytime",
        "beats": [
            {
                "label": "The Hook",
                "narration": "This is an original forum-style story about one wallet and a decision that would not leave him alone.",
                "onscreen_text": "THE WALLET TEST",
                "visual": "phone screen glowing in a dark bedroom, anonymous confession post, wallet on desk",
            },
            {
                "label": "The Find",
                "narration": "He found the wallet under a cafe table, thick with cash and one photo tucked behind the cards.",
                "onscreen_text": "HE FOUND CASH",
                "visual": "small cafe table, wallet half hidden, rain on window, cinematic 2D style",
            },
            {
                "label": "The Temptation",
                "narration": "Rent was due, his account was nearly empty, and for ten seconds the wallet felt like an answer.",
                "onscreen_text": "RENT WAS DUE",
                "visual": "apartment bills, empty bank app, hand hovering over wallet",
            },
            {
                "label": "The Photo",
                "narration": "Then he saw the photo: an old man smiling beside someone who looked exactly like the cashier.",
                "onscreen_text": "THE PHOTO HIT",
                "visual": "close-up of worn family photo, cafe cashier blurred in background",
            },
            {
                "label": "The Choice",
                "narration": "He walked to the counter, handed it over, and tried to act like the choice had been easy.",
                "onscreen_text": "HE HANDED IT BACK",
                "visual": "cashier receiving wallet, quiet cafe, warm light and awkward silence",
            },
            {
                "label": "The Message",
                "narration": "That night, a message arrived from a stranger saying the wallet belonged to her father.",
                "onscreen_text": "A MESSAGE ARRIVED",
                "visual": "phone notification, dark room, single lamp, emotional comic panel",
            },
            {
                "label": "The Twist",
                "narration": "She said the cash was for medicine, and the photo was the last one her mother ever took.",
                "onscreen_text": "THE CASH HAD A REASON",
                "visual": "medicine bag, old photograph, soft dramatic shadows",
            },
            {
                "label": "The Sting",
                "narration": "He wrote that post because doing the right thing still scared him, and that part felt honest.",
                "onscreen_text": "HONESTY WAS HEAVY",
                "visual": "anonymous post being typed, wallet gone, rain ending outside window",
            },
        ],
    },
    {
        "slug": "cat-lighthouse-animation",
        "short_title": "THE CAT AND THE LIGHT",
        "title": "The Cat Who Kept The Lighthouse Awake",
        "hook": "An animated cat followed one blinking light and accidentally saved a whole harbor.",
        "category": "cat animation",
        "beats": [
            {
                "label": "The Hook",
                "narration": "An animated cat followed one blinking light and accidentally saved a whole harbor.",
                "onscreen_text": "THE CAT SAW IT",
                "visual": "cute orange cat on wet stones below a lighthouse, moonlit 2D animation style",
            },
            {
                "label": "The Storm",
                "narration": "The storm knocked out half the village, and the lighthouse keeper slept through the first warning bell.",
                "onscreen_text": "THE STORM HIT",
                "visual": "cartoon storm, sleeping keeper, bell rope swaying, cat ears alert",
            },
            {
                "label": "The Climb",
                "narration": "The cat chased a moth up the spiral stairs, slipping, scrambling, and refusing to quit.",
                "onscreen_text": "THE CAT CLIMBED",
                "visual": "spiral lighthouse stairs, cat chasing glowing moth, playful motion blur",
            },
            {
                "label": "The Lamp",
                "narration": "At the top, the lamp flickered, and far away a boat drifted toward black rocks.",
                "onscreen_text": "THE LAMP FLICKERED",
                "visual": "cat beside giant lamp, tiny boat in storm outside window",
            },
            {
                "label": "The Crash",
                "narration": "The cat knocked over a toolbox, the keeper woke up, and the whole room exploded into panic.",
                "onscreen_text": "CHAOS WORKED",
                "visual": "tools flying, startled keeper, cat wide-eyed, comic impact lines",
            },
            {
                "label": "The Signal",
                "narration": "The keeper fixed the lamp just before the boat reached the rocks.",
                "onscreen_text": "THE LIGHT RETURNED",
                "visual": "bright lighthouse beam cutting through rain toward a boat",
            },
            {
                "label": "The Reward",
                "narration": "By morning, everyone praised the keeper, while the cat quietly stole a fish from breakfast.",
                "onscreen_text": "THE CAT GOT PAID",
                "visual": "sunrise harbor, villagers cheering, cat sneaking fish with smug face",
            },
            {
                "label": "The Sting",
                "narration": "Nobody knew the truth, except the cat, the moth, and the boat that made it home.",
                "onscreen_text": "ONLY THE CAT KNEW",
                "visual": "final cozy shot of cat in lighthouse window, warm sunrise, fish bones",
            },
        ],
    },
    {
        "slug": "shadow-town-2d-animation",
        "short_title": "THE TOWN WITHOUT SHADOWS",
        "title": "The 2D Town Where Shadows Disappeared",
        "hook": "In a tiny animated town, people panicked when their shadows disappeared before sunset.",
        "category": "2d animation moral story",
        "beats": [
            {
                "label": "The Hook",
                "narration": "In a tiny animated town, people panicked when their shadows disappeared before sunset.",
                "onscreen_text": "THE SHADOWS LEFT",
                "visual": "whimsical 2D town square, villagers staring at empty ground, warm sunset colors",
            },
            {
                "label": "The Panic",
                "narration": "The baker blamed the clockmaker, the clockmaker blamed the mayor, and everyone pointed at someone else.",
                "onscreen_text": "EVERYONE BLAMED SOMEONE",
                "visual": "cartoon villagers arguing, pointing fingers, long empty streets",
            },
            {
                "label": "The Kid",
                "narration": "Only one kid noticed the shadows were not gone; they were hiding under the oldest bridge.",
                "onscreen_text": "ONE KID NOTICED",
                "visual": "small child peeking under stone bridge, shy shadow shapes hiding",
            },
            {
                "label": "The Reason",
                "narration": "The shadows said they were tired of being stepped on by people who never looked down.",
                "onscreen_text": "THEY WERE TIRED",
                "visual": "cute shadow characters with sad faces, bridge tunnel, soft blue light",
            },
            {
                "label": "The Deal",
                "narration": "The kid promised the town would stop rushing and notice the little things again.",
                "onscreen_text": "A PROMISE FIXED IT",
                "visual": "kid shaking hands with a shadow, magical glow, villagers listening",
            },
            {
                "label": "The Return",
                "narration": "One by one, the shadows returned, but they were slightly braver than before.",
                "onscreen_text": "THE SHADOWS RETURNED",
                "visual": "shadows stretching across colorful streets, playful 2D motion",
            },
            {
                "label": "The Change",
                "narration": "After that, people walked slower, greeted each other, and watched where the sun fell.",
                "onscreen_text": "THE TOWN SLOWED DOWN",
                "visual": "peaceful town, neighbors waving, golden light and long friendly shadows",
            },
            {
                "label": "The Sting",
                "narration": "Sometimes a town does not lose magic. Sometimes it stops paying attention.",
                "onscreen_text": "ATTENTION IS MAGIC",
                "visual": "final storybook frame, town glowing, child and shadow on bridge",
            },
        ],
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


@dataclass(frozen=True)
class AlignedWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    group_words: tuple[str, ...]
    active_index: int


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
    alignment_path = output_dir / "voiceover_alignment.json"
    captions_path = output_dir / "story_captions.ass"
    captions_manifest_path = output_dir / "caption_manifest.json"
    video_path = output_dir / f"story_{sequence_index:02d}_captioned.mp4"
    poster_path = output_dir / "poster.png"
    metadata_path = output_dir / "metadata.json"
    segments_path = output_dir / "segments.json"

    story_path.write_text(json.dumps(story, indent=2), encoding="utf-8")
    script_path.write_text(_script_text(story), encoding="utf-8")

    narration = story_narration_text(story)
    log(f"Generating original English story voiceover: {story['title']}.")
    voiceover_meta = _generate_story_voiceover(narration, voiceover_path, logger=log)
    alignment_meta = _generate_story_word_alignment(
        narration,
        voiceover_path,
        alignment_path,
        logger=log,
    )
    aligned_words = _alignment_words_from_payload(alignment_meta)
    caption_cues = _caption_cues(aligned_words, max_words=CAPTION_MAX_WORDS)
    _write_story_caption_ass(caption_cues, captions_path)
    _write_caption_manifest(
        narration,
        aligned_words,
        caption_cues,
        captions_manifest_path,
        provider=str(alignment_meta.get("provider") or ""),
    )
    render_story_video(
        story,
        voiceover_path,
        video_path,
        poster_path,
        captions_path=captions_path,
        logger=log,
    )
    layout_validation_path = validate_story_video_layout(
        story,
        video_path,
        output_dir,
        captions_manifest_path=captions_manifest_path,
        logger=log,
    )

    segments = [
        {
            "start_seconds": 0.0,
            "end_seconds": round(_media_duration(video_path), 2),
            "excerpt": story["hook"],
            "story_category": story.get("category") or "",
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
        "caption_style": "forced_alignment_ass_centered_red_active_word",
        "caption_alignment": str(alignment_path),
        "captions": str(captions_path),
        "caption_manifest": str(captions_manifest_path),
        "caption_alignment_provider": alignment_meta.get("provider") or "",
        "caption_word_count": len(aligned_words),
        "poster_style": "thumbnail_safe_final_outro",
        "thumbnail_outro_seconds": THUMBNAIL_OUTRO_SECONDS,
        "layout_validation": str(layout_validation_path),
        "duration_seconds": round(_media_duration(video_path), 2),
        "voiceover": str(voiceover_path),
        "voiceover_provider": voiceover_meta.get("provider") or "",
        "voiceover_model": voiceover_meta.get("model") or "",
        "voiceover_characters": voiceover_meta.get("characters") or 0,
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
    rotation_index = _story_rotation_index(source_entry, sequence_index=sequence_index)
    ai_story = _build_ai_story(source_entry, sequence_index=rotation_index, logger=log)
    if ai_story is not None:
        return _with_opening_hook(ai_story, sequence_index=rotation_index)
    return _with_opening_hook(
        _build_library_story(source_entry, sequence_index=rotation_index),
        sequence_index=rotation_index,
    )


def story_rotation_size() -> int:
    return len(GENRE_ROTATION)


def _story_rotation_index(source_entry: dict[str, Any], *, sequence_index: int) -> int:
    source_url = str(source_entry.get("source_url") or "").strip()
    match = STORY_ROTATION_SUFFIX.search(source_url)
    offset = int(match.group("offset")) if match else 0
    return max(1, sequence_index) + offset


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


def _topic_matches_lane(topic: dict[str, Any], lane: str) -> bool:
    category = str(topic.get("category") or "true history story").lower()
    title = str(topic.get("title") or "").lower()
    haystack = f"{category} {title} {topic.get('slug', '')}".lower()
    lane_lower = lane.lower()
    if "lawsuit" in lane_lower:
        return "lawsuit" in haystack
    if "court" in lane_lower:
        return "court" in haystack or "legal" in haystack
    if "reddit" in lane_lower or "storytime" in lane_lower or "forum" in lane_lower:
        return "reddit" in haystack or "forum" in haystack or "storytime" in haystack
    if "cat" in lane_lower:
        return "cat" in haystack
    if "economy" in lane_lower:
        return "economy" in haystack or "market" in haystack or "bubble" in haystack
    if "2d" in lane_lower or "animation" in lane_lower:
        return "2d" in haystack or "animation" in haystack
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
                    "Return strict JSON only. Use well-known public-domain history, documented mysteries, public legal cases, "
                    "world economy explainers, original animated fables, original cat animation stories, or original "
                    "forum-confession style stories. Avoid copyrighted fiction, copied Reddit posts, explicit gore, "
                    "current-news claims, unsupported accusations, and invented events presented as real."
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
        "Rotate across true history, mystery stories, lawsuits, court cases, original forum-confession storytime, "
        "cat animations, world economy stories, 2D animation fables, eerie folklore, survival stories, ancient mysteries, "
        "lost places, and dark biographies.\n"
        f"Do not reuse these fallback examples directly: {previous_topics}.\n"
        "Requirements:\n"
        "- 8 beats exactly.\n"
        "- Each beat narration is 16 to 27 spoken words, simple and punchy.\n"
        "- Total script should feel like a 60 to 75 second story.\n"
        "- First beat narration must begin with a curiosity hook like: Did you know this actually happened? / Have you ever heard this story? / What if I told you this was real?\n"
        "- Then continue with setup, pressure, turn, consequence, final sting.\n"
        "- For history, lawsuits, court cases, and economy stories: use real, widely known public subjects and do not invent causes, dates, rulings, or places.\n"
        "- For forum/reddit-style stories: make it original fiction and never claim it came from a real Reddit post.\n"
        "- For cat animation and 2D animation: make it original, visual, cute or emotional, and clearly animated.\n"
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
    captions_path: Path | None = None,
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
        _render_or_reuse_story_beat(
            ffmpeg,
            story,
            beat,
            index=index,
            total=len(beats),
            duration=duration,
            output_path=segment_path,
            background_path=background_path,
            logger=log,
        )
        segment_paths.append(segment_path)

    outro_path = segments_dir / "beat_99_thumbnail_outro.mp4"
    thumbnail_outro_seconds = min(0.95, max(0.2, THUMBNAIL_OUTRO_SECONDS))
    _render_thumbnail_outro_segment(ffmpeg, poster_path, outro_path, thumbnail_outro_seconds)
    segment_paths.append(outro_path)
    concat_path = output_dir / "concat.txt"
    concat_path.write_text(_concat_file(segment_paths), encoding="utf-8")
    _merge_segments_with_audio(
        ffmpeg,
        concat_path,
        voiceover_path,
        video_path,
        story_duration + thumbnail_outro_seconds,
        captions_path=captions_path,
    )


def validate_story_video_layout(
    story: dict[str, Any],
    video_path: Path,
    output_dir: Path,
    *,
    captions_manifest_path: Path | None = None,
    logger: Callable[[str], None] | None = None,
) -> Path:
    report_path = output_dir / "layout_validation.json"
    log = logger or (lambda message: None)
    disabled = os.getenv("TIKTOK_STORY_VALIDATE_LAYOUT", "true").strip().lower() in AI_STORY_DISABLED_VALUES
    issues = [] if disabled else _caption_layout_issues(story)
    if not disabled and captions_manifest_path is not None:
        issues.extend(_caption_manifest_issues(captions_manifest_path))
    frames: list[str] = []
    frame_errors: list[str] = []
    if not disabled:
        ffmpeg = _ffmpeg()
        if ffmpeg and video_path.exists():
            try:
                frames = [str(path) for path in _extract_validation_frames(ffmpeg, video_path, output_dir)]
            except Exception as exc:
                frame_errors.append(str(exc)[:500])
    report = {
        "ok": not issues,
        "skipped": disabled,
        "render_version": RENDER_VERSION,
        "video": str(video_path),
        "caption_safe_left": CAPTION_SAFE_LEFT,
        "caption_safe_right": CAPTION_SAFE_RIGHT,
        "caption_safe_width": CAPTION_SAFE_WIDTH,
        "caption_max_words": CAPTION_MAX_WORDS,
        "caption_min_word_gap": CAPTION_MIN_WORD_GAP,
        "caption_manifest": str(captions_manifest_path or ""),
        "issues": issues,
        "validation_frames": frames,
        "frame_errors": frame_errors,
        "created_at": _utc_now(),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if issues:
        raise RuntimeError(f"Story caption layout validation failed; see {report_path}")
    if disabled:
        log("Story layout validation skipped by TIKTOK_STORY_VALIDATE_LAYOUT.")
    else:
        log(f"Story layout validation passed: {report_path.name}.")
    return report_path


def story_narration_text(story: dict[str, Any]) -> str:
    return " ".join(str(beat.get("narration") or "").strip() for beat in story.get("beats") or []).strip()


def _generate_story_voiceover(
    narration: str,
    output_path: Path,
    *,
    logger: Callable[[str], None],
) -> dict[str, Any]:
    provider = os.getenv("TIKTOK_STORY_TTS_PROVIDER", "auto").strip().lower() or "auto"
    if provider in {"elevenlabs", "11labs"}:
        return _generate_elevenlabs_voiceover(narration, output_path, logger=logger)
    if provider in {"openai", "openai_tts"}:
        return _generate_openai_voiceover(narration, output_path, logger=logger)
    if provider not in {"auto", "best"}:
        logger(f"Unknown TTS provider {provider}; using auto fallback.")

    if os.getenv("ELEVENLABS_API_KEY", "").strip():
        try:
            return _generate_elevenlabs_voiceover(narration, output_path, logger=logger)
        except Exception as exc:
            logger(f"ElevenLabs TTS failed, falling back to OpenAI TTS: {_safe_tts_error(exc)}")
    return _generate_openai_voiceover(narration, output_path, logger=logger)


def _generate_elevenlabs_voiceover(
    narration: str,
    output_path: Path,
    *,
    logger: Callable[[str], None],
) -> dict[str, Any]:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required for ElevenLabs voiceover.")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVENLABS_VOICE_ID).strip() or DEFAULT_ELEVENLABS_VOICE_ID
    model = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5").strip() or "eleven_flash_v2_5"
    output_format = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "mp3_44100_128").strip() or "mp3_44100_128"
    reservation = _reserve_elevenlabs_credits(narration, output_path, model=model)
    if not reservation.allowed:
        raise RuntimeError(
            "ElevenLabs weekly budget guard blocked this story "
            f"({reservation.reason}; estimated {reservation.estimated_credits:.1f} credits)."
        )
    ledger_path = _elevenlabs_shared_ledger_path(output_path)
    url = f"{ELEVENLABS_TTS_URL.format(voice_id=voice_id)}?output_format={output_format}"
    payload = {
        "text": narration,
        "model_id": model,
        "voice_settings": {
            "stability": _env_float("ELEVENLABS_STABILITY", 0.52, minimum=0.0, maximum=1.0),
            "similarity_boost": _env_float("ELEVENLABS_SIMILARITY_BOOST", 0.78, minimum=0.0, maximum=1.0),
            "style": _env_float("ELEVENLABS_STYLE", 0.08, minimum=0.0, maximum=1.0),
            "use_speaker_boost": os.getenv("ELEVENLABS_USE_SPEAKER_BOOST", "true").strip().lower()
            not in AI_STORY_DISABLED_VALUES,
        },
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            audio = response.read()
            actual_credits = _optional_float(response.getheader("character-cost", None))
    except HTTPError as exc:
        release_reservation(ledger_path, reservation.reservation_id)
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs TTS error {exc.code}: {_safe_openai_error(body)}") from exc
    except URLError as exc:
        release_reservation(ledger_path, reservation.reservation_id)
        raise RuntimeError(f"ElevenLabs TTS network error: {exc.reason}") from exc
    except Exception:
        release_reservation(ledger_path, reservation.reservation_id)
        raise

    characters = len(narration)
    committed_credits = actual_credits if actual_credits is not None else reservation.estimated_credits
    commit_reservation(ledger_path, reservation.reservation_id, actual_credits=committed_credits)
    if not audio:
        raise RuntimeError("ElevenLabs voiceover did not return audio data.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("ElevenLabs voiceover did not produce an audio file.")

    logger(
        f"ElevenLabs voiceover generated with {model}: {characters} characters, "
        f"{committed_credits:.1f} shared credits."
    )
    return {
        "provider": "elevenlabs",
        "model": model,
        "voice_id": voice_id,
        "characters": characters,
        "credits": round(committed_credits, 3),
        "output_format": output_format,
    }


def _generate_openai_voiceover(
    narration: str,
    output_path: Path,
    *,
    logger: Callable[[str], None],
) -> dict[str, Any]:
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
        model = fallback_model
        voice = fallback_voice
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("OpenAI voiceover did not produce an audio file.")
    return {
        "provider": "openai",
        "model": model,
        "voice": voice,
        "characters": len(narration),
    }


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


def _generate_story_word_alignment(
    narration: str,
    voiceover_path: Path,
    alignment_path: Path,
    *,
    logger: Callable[[str], None],
) -> dict[str, Any]:
    duration = _media_duration(voiceover_path)
    attempts: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if elevenlabs_key:
        attempts.append(
            (
                "elevenlabs_forced_alignment",
                lambda: _elevenlabs_word_alignment(elevenlabs_key, narration, voiceover_path),
            )
        )
    if openai_key:
        attempts.append(
            (
                "openai_whisper_words",
                lambda: _openai_word_alignment(openai_key, narration, voiceover_path),
            )
        )
    if not attempts:
        raise RuntimeError("No word-alignment provider is configured; refusing to render an uncaptioned story.")

    expected_count = len(_caption_tokens(narration))
    errors: list[str] = []
    for provider, align in attempts:
        try:
            raw_words = align()
            words = _normalized_alignment_words(narration, raw_words, duration=duration)
            coverage = len(words) / max(1, expected_count)
            if len(words) != expected_count:
                raise RuntimeError(
                    f"alignment covered {len(words)}/{expected_count} narration words"
                )
            payload = {
                "provider": provider,
                "duration": round(duration, 3),
                "expected_word_count": expected_count,
                "aligned_word_count": len(words),
                "coverage": round(min(1.0, coverage), 4),
                "words": [
                    {"text": word.text, "start": round(word.start, 3), "end": round(word.end, 3)}
                    for word in words
                ],
                "created_at": _utc_now(),
            }
            alignment_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = alignment_path.with_suffix(alignment_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(alignment_path)
            logger(
                f"Word alignment ready from {provider}: {len(words)}/{expected_count} narration words."
            )
            return payload
        except Exception as exc:
            errors.append(f"{provider}: {_safe_tts_error(exc)}")
            logger(f"Word alignment attempt failed for {provider}: {_safe_tts_error(exc)}")
    raise RuntimeError(
        "Word alignment failed; refusing to render or upload a story with missing or mistimed captions. "
        + "; ".join(errors)
    )


def _elevenlabs_word_alignment(api_key: str, narration: str, audio_path: Path) -> list[dict[str, Any]]:
    boundary = f"TikTokStoryAlignment{os.urandom(12).hex()}"
    body = _alignment_multipart_body(
        boundary,
        fields={"text": narration},
        files={"file": (audio_path.name, "audio/mpeg", audio_path.read_bytes())},
    )
    request = Request(
        ELEVENLABS_FORCED_ALIGNMENT_URL,
        data=body,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=240) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs alignment error {exc.code}: {_safe_openai_error(detail)}") from exc
    except URLError as exc:
        raise RuntimeError(f"ElevenLabs alignment network error: {exc.reason}") from exc
    payload = _alignment_json(raw, provider="ElevenLabs")
    words = payload.get("words")
    if not isinstance(words, list):
        raise RuntimeError("ElevenLabs alignment did not return word timestamps.")
    return [word for word in words if isinstance(word, dict)]


def _openai_word_alignment(api_key: str, narration: str, audio_path: Path) -> list[dict[str, Any]]:
    boundary = f"TikTokStoryWhisper{os.urandom(12).hex()}"
    body = _alignment_multipart_body(
        boundary,
        fields={
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "language": "en",
            "prompt": narration[:2200],
        },
        files={"file": (audio_path.name, "audio/mpeg", audio_path.read_bytes())},
    )
    request = Request(
        OPENAI_TRANSCRIPTIONS_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=240) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI word alignment error {exc.code}: {_safe_openai_error(detail)}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI word alignment network error: {exc.reason}") from exc
    payload = _alignment_json(raw, provider="OpenAI")
    words = payload.get("words")
    if not isinstance(words, list):
        raise RuntimeError("OpenAI word alignment did not return word timestamps.")
    return [word for word in words if isinstance(word, dict)]


def _alignment_json(raw: str, *, provider: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except ValueError as exc:
        raise RuntimeError(f"{provider} alignment returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} alignment returned an invalid response.")
    return payload


def _alignment_multipart_body(
    boundary: str,
    *,
    fields: dict[str, str],
    files: dict[str, tuple[str, str, bytes]],
) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )
    for name, (filename, content_type, data) in files.items():
        parts.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode("utf-8"),
                f"Content-Type: {content_type}".encode("utf-8"),
                b"",
                data,
            ]
        )
    parts.extend([f"--{boundary}--".encode("utf-8"), b""])
    return b"\r\n".join(parts)


def _normalized_alignment_words(
    narration: str,
    raw_words: list[dict[str, Any]],
    *,
    duration: float,
) -> list[AlignedWord]:
    expected = _caption_tokens(narration)
    usable: list[tuple[str, float, float]] = []
    for raw_word in raw_words:
        text = _clean(raw_word.get("text") or raw_word.get("word") or "")
        start = _optional_float(raw_word.get("start"))
        end = _optional_float(raw_word.get("end"))
        if not text or start is None or end is None:
            continue
        safe_start = max(0.0, min(float(duration), start))
        safe_end = max(safe_start + 0.03, min(float(duration), end))
        usable.append((text, safe_start, safe_end))
    usable.sort(key=lambda item: (item[1], item[2]))
    if not usable:
        raise RuntimeError("Word alignment returned no usable words.")

    preserve_script_words = len(usable) == len(expected)
    words: list[AlignedWord] = []
    previous_start = -1.0
    for index, (aligned_text, start, end) in enumerate(usable):
        safe_start = max(previous_start, start)
        safe_end = max(safe_start + 0.03, end)
        display = expected[index] if preserve_script_words else aligned_text
        words.append(AlignedWord(text=display, start=safe_start, end=safe_end))
        previous_start = safe_start
    return words


def _alignment_words_from_payload(payload: dict[str, Any]) -> list[AlignedWord]:
    words: list[AlignedWord] = []
    for raw_word in payload.get("words") or []:
        if not isinstance(raw_word, dict):
            continue
        text = _clean(raw_word.get("text") or "")
        start = _optional_float(raw_word.get("start"))
        end = _optional_float(raw_word.get("end"))
        if text and start is not None and end is not None and end > start:
            words.append(AlignedWord(text=text, start=start, end=end))
    if not words:
        raise RuntimeError("Saved word alignment is empty.")
    return words


def _caption_tokens(text: str) -> list[str]:
    return [word for word in re.split(r"\s+", _clean(text)) if word]


def _reserve_elevenlabs_credits(
    narration: str,
    output_path: Path,
    *,
    model: str = "eleven_flash_v2_5",
) -> BudgetDecision:
    characters = len(narration)
    max_story_chars = _env_int("ELEVENLABS_MAX_STORY_CHARS", 1600, minimum=200, maximum=5000)
    if characters > max_story_chars:
        return BudgetDecision(
            allowed=False,
            reason="story_character_limit",
            reservation_id="",
            week_start="",
            estimated_credits=0.0,
            shared_used_credits=0.0,
            pipeline_used_credits=0.0,
            shared_weekly_credit_budget=0.0,
            pipeline_weekly_credit_budget=0.0,
        )
    return reserve_credits(
        _elevenlabs_shared_ledger_path(output_path),
        pipeline="tiktok_english",
        input_characters=characters,
        model=model,
        shared_weekly_credit_budget=_env_float(
            "ELEVENLABS_SHARED_WEEKLY_CREDIT_BUDGET",
            6000.0,
            minimum=0.0,
            maximum=10_000_000.0,
        ),
        pipeline_weekly_credit_budget=_env_float(
            "ELEVENLABS_PIPELINE_WEEKLY_CREDIT_BUDGET",
            4000.0,
            minimum=0.0,
            maximum=10_000_000.0,
        ),
        credits_per_character=_env_float(
            "ELEVENLABS_CREDITS_PER_CHARACTER",
            1.0,
            minimum=0.0,
            maximum=10.0,
        ),
    )


def _elevenlabs_shared_ledger_path(output_path: Path) -> Path:
    configured = os.getenv("ELEVENLABS_SHARED_LEDGER_PATH", "").strip()
    if configured:
        return Path(configured)
    for parent in output_path.resolve().parents:
        if (parent / ".secrets").exists():
            return parent / ".secrets" / "elevenlabs_shared_usage.sqlite3"
    return output_path.parent / "elevenlabs_shared_usage.sqlite3"


def _optional_float(value: Any) -> float | None:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _safe_tts_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", text)
    text = re.sub(r"xi-[A-Za-z0-9_-]+", "xi-***", text)
    return text[:500]


def _beats_for_topic(topic: dict[str, Any]) -> list[dict[str, str]]:
    custom_beats = topic.get("beats")
    if isinstance(custom_beats, list) and custom_beats:
        return [
            _with_default_visual(topic, beat, index)
            for index, beat in enumerate(custom_beats, start=1)
            if isinstance(beat, dict)
        ]
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


def _with_default_visual(topic: dict[str, Any], beat: dict[str, str], index: int) -> dict[str, str]:
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


def _default_visual_palette(topic: dict[str, Any], index: int) -> str:
    category = str(topic.get("category") or "").lower()
    if "cat" in category:
        return "warm amber, moon blue, cozy orange, playful shadows"
    if "2d" in category or "animation" in category:
        return "storybook gold, soft blue, candy red, clean ink"
    if "reddit" in category or "forum" in category or "storytime" in category:
        return "phone glow blue, bedroom shadow, cafe amber, honest mood"
    if "lawsuit" in category or "court" in category:
        return "courtroom brown, paper cream, warning red, deep shadow"
    if "economy" in category:
        return "market green, coin gold, paper cream, danger red"
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
    safe_duration = max(0.5, duration)
    _render_video_atomically(
        lambda preset, target: [
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
            f"{safe_duration:.3f}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            "19",
            "-movflags",
            "+faststart",
            str(target),
        ],
        output_path,
        expected_duration=safe_duration,
        primary_timeout=_env_int(
            "TIKTOK_STORY_BEAT_RENDER_TIMEOUT_SECONDS",
            420,
            minimum=120,
            maximum=1800,
        ),
        retry_timeout=_env_int(
            "TIKTOK_STORY_BEAT_RETRY_TIMEOUT_SECONDS",
            420,
            minimum=120,
            maximum=1800,
        ),
    )


def _render_or_reuse_story_beat(
    ffmpeg: str,
    story: dict[str, Any],
    beat: dict[str, str],
    *,
    index: int,
    total: int,
    duration: float,
    output_path: Path,
    background_path: Path,
    logger: Callable[[str], None],
) -> None:
    if _valid_video_file(output_path, min_duration=max(0.2, duration * 0.92)):
        logger(f"Reusing completed story beat {index}/{total}.")
        return
    logger(f"Rendering story beat {index}/{total}.")
    _render_beat_segment(
        ffmpeg,
        story,
        beat,
        index,
        total,
        duration,
        output_path,
        background_path,
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


def _merge_segments_with_audio(
    ffmpeg: str,
    concat_path: Path,
    voiceover_path: Path,
    output_path: Path,
    duration: float,
    *,
    captions_path: Path | None = None,
) -> None:
    if captions_path is None or not captions_path.exists():
        raise RuntimeError("A complete ASS caption track is required before final story rendering.")
    escaped_captions = _escape_ffmpeg_filter_path(captions_path.resolve())
    filter_complex = (
        f"[0:v]ass=filename='{escaped_captions}'[vout];"
        "[1:a]volume=1.0[a0];[2:a]volume=0.030[a1];"
        "[a0][a1]amix=inputs=2:duration=longest:dropout_transition=2[aout]"
    )
    _render_video_atomically(
        lambda preset, target: [
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
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
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
            str(target),
        ],
        output_path,
        expected_duration=duration,
        primary_timeout=_env_int(
            "TIKTOK_STORY_FINAL_RENDER_TIMEOUT_SECONDS",
            900,
            minimum=300,
            maximum=3600,
        ),
        retry_timeout=_env_int(
            "TIKTOK_STORY_FINAL_RETRY_TIMEOUT_SECONDS",
            600,
            minimum=300,
            maximum=3600,
        ),
    )


def _render_video_atomically(
    command_builder: Callable[[str, Path], list[str]],
    output_path: Path,
    *,
    expected_duration: float,
    primary_timeout: int,
    retry_timeout: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.rendering{output_path.suffix or '.mp4'}")
    attempts = (("veryfast", primary_timeout), ("ultrafast", retry_timeout))
    errors: list[str] = []
    for preset, timeout in attempts:
        temporary.unlink(missing_ok=True)
        try:
            _run(command_builder(preset, temporary), timeout=timeout)
            if not _valid_video_file(
                temporary,
                min_duration=max(0.1, expected_duration * 0.92),
            ):
                raise RuntimeError("FFmpeg produced an incomplete or unreadable MP4.")
            temporary.replace(output_path)
            return
        except Exception as exc:
            errors.append(f"{preset}: {_render_error_summary(exc)}")
            temporary.unlink(missing_ok=True)
    raise RuntimeError(
        f"{output_path.name} render failed after {len(attempts)} attempts: " + "; ".join(errors)
    )


def _escape_ffmpeg_filter_path(path: Path) -> str:
    return (
        str(path)
        .replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
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
    category = str(story.get("category") or "").lower()
    if "cat" in category:
        default_style = (
            "vertical 9:16 charming 2D animated cat story frame, clean bold outlines, expressive cartoon animal acting, "
            "cinematic lighting, cozy but high-contrast TikTok composition, clear lower-third room for captions"
        )
    elif "2d" in category or "animation" in category:
        default_style = (
            "vertical 9:16 polished 2D animation storybook frame, clean ink outlines, expressive simple characters, "
            "bright cinematic colors, emotional readable action, lower-third room for captions"
        )
    elif "reddit" in category or "forum" in category or "storytime" in category:
        default_style = (
            "vertical 9:16 modern illustrated storytime frame, cinematic 2D comic style, phone glow, everyday rooms, "
            "expressive original characters, no readable UI text, lower-third room for captions"
        )
    elif "court" in category or "lawsuit" in category:
        default_style = (
            "vertical 9:16 high-detail courtroom comic illustration, legal documents as abstract props, serious mood, "
            "clean black ink outlines, cinematic shadows, lower-third room for captions"
        )
    elif "economy" in category:
        default_style = (
            "vertical 9:16 high-detail comic explainer illustration about markets and money, symbolic charts and coins, "
            "clean black ink outlines, cinematic color, lower-third room for captions"
        )
    else:
        default_style = (
            "vertical 9:16 high-detail comic-book historical illustration, thick clean black ink outlines, "
            "flat cinematic colors, dramatic shadows, expressive non-photorealistic characters, rich background detail, "
            "TikTok-ready composition with clear subject in the center and room for captions in the lower third"
        )
    style = brand_style or (
        default_style
    )
    return (
        f"{style}. "
        "No text, no captions, no logos, no watermarks, no readable documents, no speech bubbles, no UI. "
        "Avoid gore and graphic violence. Avoid photorealism and use original illustrated character designs. "
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
    if index == 1:
        filters.append(_drawtext_center(_story_badge(story), 1140, 30, "white@0.82", max_chars=18, borderw=4))
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
    if "LAWSUIT" in category:
        return "LAWSUIT STORY"
    if "COURT" in category or "LEGAL" in category:
        return "COURT CASE"
    if "REDDIT" in category or "FORUM" in category or "STORYTIME" in category:
        return "STORYTIME"
    if "CAT" in category:
        return "CAT ANIMATION"
    if "ECONOMY" in category or "MARKET" in category:
        return "ECONOMY STORY"
    if "2D" in category or "ANIMATION" in category:
        return "2D STORY"
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


def _caption_cues(words: list[AlignedWord], *, max_words: int) -> list[CaptionCue]:
    if not words:
        return []
    group_size = max(1, max_words)
    cues: list[CaptionCue] = []
    previous_end = 0.0
    for index, word in enumerate(words):
        group_start = (index // group_size) * group_size
        group = words[group_start : group_start + group_size]
        start = max(previous_end, word.start)
        if index + 1 < len(words):
            boundary = words[index + 1].start
        else:
            boundary = word.end
        end = max(start + 0.04, boundary)
        cues.append(
            CaptionCue(
                start=round(start, 3),
                end=round(end, 3),
                group_words=tuple(item.text for item in group),
                active_index=index - group_start,
            )
        )
        previous_end = end
    return cues


def _write_story_caption_ass(cues: list[CaptionCue], path: Path) -> None:
    if not cues:
        raise RuntimeError("Cannot write an empty subtitle track.")
    font_name = Path(_font_path().replace("\\:", ":")).stem if _font_path() else "Arial"
    header = [
        "[Script Info]",
        "Title: TikTok Story Word Captions",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "; Alignment=2",
        f"; MarginL={CAPTION_ASS_MARGIN}",
        f"; MarginR={CAPTION_ASS_MARGIN}",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: StoryCaption,{font_name},{CAPTION_ASS_FONT_SIZE},&H00FFFFFF,&H00FF4D5E,"
            "&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,0,2,"
            f"{CAPTION_ASS_MARGIN},{CAPTION_ASS_MARGIN},{CAPTION_ASS_MARGIN_V},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = [
        (
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},StoryCaption,,0,0,0,,"
            f"{_caption_ass_text(cue)}"
        )
        for cue in cues
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    temporary.replace(path)


def _caption_ass_text(cue: CaptionCue) -> str:
    words = [_caption_display_word(word) for word in cue.group_words]
    lines = _caption_group_lines(words, size=CAPTION_ASS_FONT_SIZE)
    fitted_size = _caption_fitted_font_size(lines, CAPTION_ASS_FONT_SIZE)
    lines = _caption_group_lines(words, size=fitted_size)
    output: list[str] = [f"{{\\fs{fitted_size}\\bord5\\shad0}}"]
    cursor = 0
    for line_index, line in enumerate(lines):
        if line_index:
            output.append(r"\N")
        for word_index, word in enumerate(line):
            if word_index:
                output.append(" ")
            color = CAPTION_ASS_ACTIVE_COLOR if cursor == cue.active_index else CAPTION_ASS_BASE_COLOR
            output.append(f"{{\\c{color}}}{_escape_ass_text(word)}")
            cursor += 1
    return "".join(output)


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _write_caption_manifest(
    narration: str,
    words: list[AlignedWord],
    cues: list[CaptionCue],
    path: Path,
    *,
    provider: str,
) -> None:
    payload = {
        "provider": provider,
        "expected_word_count": len(_caption_tokens(narration)),
        "aligned_word_count": len(words),
        "cue_count": len(cues),
        "all_words_present": len(words) == len(_caption_tokens(narration)),
        "non_overlapping": all(left.end <= right.start for left, right in zip(cues, cues[1:])),
        "centered": True,
        "max_words_per_group": CAPTION_MAX_WORDS,
        "words": [word.text for word in words],
        "cues": [
            {
                "start": cue.start,
                "end": cue.end,
                "group_words": list(cue.group_words),
                "active_index": cue.active_index,
            }
            for cue in cues
        ],
        "created_at": _utc_now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _caption_manifest_issues(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [{"type": "caption_manifest_missing", "error": str(exc)[:300]}]
    issues: list[dict[str, Any]] = []
    expected = int(payload.get("expected_word_count") or 0)
    aligned = int(payload.get("aligned_word_count") or 0)
    cue_count = int(payload.get("cue_count") or 0)
    if expected <= 0 or aligned != expected:
        issues.append(
            {
                "type": "caption_word_coverage",
                "expected_word_count": expected,
                "aligned_word_count": aligned,
            }
        )
    if cue_count != aligned:
        issues.append(
            {
                "type": "caption_cue_coverage",
                "cue_count": cue_count,
                "aligned_word_count": aligned,
            }
        )
    if not payload.get("non_overlapping"):
        issues.append({"type": "caption_cue_overlap"})
    if not payload.get("centered"):
        issues.append({"type": "caption_not_centered"})
    return issues


def _caption_layout_issues(story: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    beats = [beat for beat in story.get("beats") or [] if isinstance(beat, dict)]
    for beat_index, beat in enumerate(beats, start=1):
        text = _caption_text_for_beat(beat)
        groups = _caption_word_groups(text.upper(), max_words=CAPTION_MAX_WORDS)
        for group_index, group in enumerate(groups, start=1):
            draw_group = [_caption_display_word(word) for word in group]
            lines = _caption_group_lines(draw_group, size=72)
            _, size, _ = _caption_group_layout(len(lines))
            lines = _caption_group_lines(draw_group, size=size)
            size = _caption_fitted_font_size(lines, size)
            for line_index, line_words in enumerate(lines, start=1):
                line_width = _caption_line_width(line_words, size)
                if len(line_words) > CAPTION_MAX_WORDS:
                    issues.append(
                        {
                            "type": "too_many_words",
                            "beat": beat_index,
                            "group": group_index,
                            "line": line_index,
                            "words": line_words,
                            "max_words": CAPTION_MAX_WORDS,
                        }
                    )
                if line_width > CAPTION_SAFE_WIDTH:
                    issues.append(
                        {
                            "type": "line_overflow",
                            "beat": beat_index,
                            "group": group_index,
                            "line": line_index,
                            "words": line_words,
                            "line_width": line_width,
                            "safe_width": CAPTION_SAFE_WIDTH,
                            "font_size": size,
                        }
                    )
                slots = _caption_line_slots(line_words, size)
                for slot_index in range(1, len(slots)):
                    previous_end = slots[slot_index - 1][0] + slots[slot_index - 1][1]
                    gap = slots[slot_index][0] - previous_end
                    if gap < CAPTION_MIN_WORD_GAP:
                        issues.append(
                            {
                                "type": "word_gap_too_small",
                                "beat": beat_index,
                                "group": group_index,
                                "line": line_index,
                                "words": line_words,
                                "gap": gap,
                                "min_gap": CAPTION_MIN_WORD_GAP,
                                "font_size": size,
                            }
                        )
    return issues


def _karaoke_caption_filters(story: dict[str, Any], beat: dict[str, str], *, duration: float, index: int) -> list[str]:
    text = _caption_text_for_beat(beat)
    groups = _caption_word_groups(text.upper(), max_words=CAPTION_MAX_WORDS)
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
                active_enable = f"{group_enable}*{_between(start, end)}"
                inactive_enable = f"{group_enable}*not({active_enable})"
                filters.append(
                    _drawtext(
                        safe_word,
                        word_x,
                        y,
                        size,
                        WHITE,
                        max_chars=18,
                        borderw=CAPTION_OUTLINE_WIDTH,
                        enable=inactive_enable,
                    )
                )
                filters.append(
                    _drawtext(
                        safe_word,
                        word_x,
                        y,
                        size,
                        RED,
                        max_chars=18,
                        borderw=CAPTION_ACTIVE_OUTLINE_WIDTH,
                        enable=active_enable,
                    )
                )
                group_window_index += 1
    if index == 1:
        filters.append(_drawtext_center(_story_badge(story), first_caption_y - 78, 32, "white@0.86", max_chars=18, borderw=4))
    return filters


def _caption_text_for_beat(beat: dict[str, str]) -> str:
    """Use the spoken narration so captions never omit words from the voiceover."""
    for key in ("narration", "onscreen_text", "label"):
        value = _clean(beat.get(key) or "")
        if value:
            return value
    return "STORY"


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
    return _clean(word).replace("'", chr(8217)).replace("\n", " ").strip()


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
    gap = max(CAPTION_MIN_WORD_GAP, int(size * CAPTION_WORD_GAP_RATIO))
    cursor = 0
    slots: list[tuple[int, int]] = []
    for word in words:
        width = _text_width(_caption_display_word(word), size) + CAPTION_SLOT_RIGHT_PAD
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
    safe_end = max(start + 0.01, end)
    return f"gte(t\\,{start:.2f})*lt(t\\,{safe_end:.2f})"


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


def _valid_video_file(path: Path, *, min_duration: float) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    ffprobe = _ffprobe()
    if not ffprobe:
        return True
    try:
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
        duration = float((completed.stdout or "").strip())
    except (OSError, TypeError, ValueError, RuntimeError):
        return False
    return duration >= max(0.1, min_duration)


def _extract_validation_frames(ffmpeg: str, video_path: Path, output_dir: Path) -> list[Path]:
    frame_dir = output_dir / "validation_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    duration = _media_duration(video_path)
    if duration <= 1.0:
        timestamps = [0.0]
    else:
        latest = max(0.2, duration - 0.35)
        timestamps = [min(latest, max(0.15, duration * ratio)) for ratio in (0.18, 0.5, 0.82)]
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(timestamps, start=1):
        frame_path = frame_dir / f"frame_{index:02d}.jpg"
        _run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{timestamp:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ],
            timeout=90,
        )
        frame_paths.append(frame_path)
    return frame_paths


def _concat_file(paths: list[Path]) -> str:
    return "".join(
        f"file '{str(path.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
        for path in paths
    )


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        executable = Path(command[0]).name if command else "command"
        target = Path(command[-1]).name if command else "output"
        raise RuntimeError(f"{executable} timed out after {timeout} seconds while rendering {target}.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-8:])
        raise RuntimeError(tail or str(exc)) from exc


def _render_error_summary(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return text[:320] or exc.__class__.__name__


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

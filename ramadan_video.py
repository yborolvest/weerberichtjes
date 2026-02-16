"""
Ramadan video generator: daily verse from the Quran (Arabic) with random background.
Uses https://quranapi.pages.dev/ for verses and same S3 solution for backgrounds (5 random).
Ramadan start: 17 February 2025.
"""
import os
import random
import math
import json
import tempfile
import glob
import wave
from datetime import datetime, date
from typing import Optional

import requests
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy.video.VideoClip import ImageClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.audio.AudioClip import CompositeAudioClip
from moviepy.video.fx import CrossFadeIn

# ---------- PATHS & CONFIG ----------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")
BACKGROUND_DIR = os.path.join(BASE_DIR, "video_parts", "backgrounds")
AVATAR_IMAGE = os.path.join(BASE_DIR, "avatar.png")
MUSIC_DIR = os.path.join(BASE_DIR, "music")
EXTRA_TAIL_SECONDS = 5.0
NORMAL_TEXT_FONT_SIZE = 40
SUBTITLE_BORDER_WIDTH = 3

# ---------- VOICE & SYLLABLES (for gibberish intro) ----------

VOWELS = set("aeiouáéíóúäëïöüAEIOU")


def _split_word_into_syllables(word: str):
    """Split a word into chunks that each contain at least one vowel (for timing)."""
    syllables = []
    start = 0
    i = 0
    n = len(word)
    while i < n:
        has_vowel = False
        while i < n:
            if word[i] in VOWELS:
                has_vowel = True
            i += 1
            if has_vowel:
                break
        while i < n and word[i] not in VOWELS:
            if all(ch not in VOWELS for ch in word[i:]):
                i = n
                break
            i += 1
        syllables.append(word[start:i])
        start = i
    if start < n:
        syllables.append(word[start:n])
    return syllables


def split_into_syllable_tokens(text: str):
    """Split text into syllable-like tokens, preserving spaces and punctuation. Joining tokens reproduces the original."""
    tokens = []
    current_word = ""
    for ch in text:
        if ch.isspace():
            if current_word:
                tokens.extend(_split_word_into_syllables(current_word))
                current_word = ""
            tokens.append(ch)
        elif ch.isalpha():
            current_word += ch
        else:
            if current_word:
                sylls = _split_word_into_syllables(current_word)
                if sylls:
                    sylls[-1] = sylls[-1] + ch
                    tokens.extend(sylls)
                else:
                    tokens.append(ch)
                current_word = ""
            else:
                if tokens and not tokens[-1].isspace():
                    tokens[-1] = tokens[-1] + ch
                else:
                    tokens.append(ch)
    if current_word:
        tokens.extend(_split_word_into_syllables(current_word))
    return tokens


def create_gibberish_voice(text: str, voices_dir: str, out_file: str):
    """Build gibberish voice WAV from syllable clips and save timing JSON for subtitles."""
    if not os.path.isdir(voices_dir):
        raise FileNotFoundError(f"Voice clips directory not found: {voices_dir}")
    files = [os.path.join(voices_dir, f) for f in os.listdir(voices_dir) if f.lower().endswith(".wav")]
    if not files:
        raise FileNotFoundError(f"No .wav clips in '{voices_dir}'")
    tokens = split_into_syllable_tokens(text)
    combined_frames = bytearray()
    chosen_params = None
    current_time = 0.0
    syllable_events = []

    def append_silence(seconds: float):
        nonlocal combined_frames, chosen_params
        if chosen_params is None or seconds <= 0:
            return
        nframes = int(chosen_params.framerate * seconds)
        silence = b"\x00" * nframes * chosen_params.nchannels * chosen_params.sampwidth
        combined_frames.extend(silence)

    for idx, tok in enumerate(tokens):
        if tok.isspace():
            append_silence(0.05)
            current_time += 0.05
            continue
        path = random.choice(files)
        with wave.open(path, "rb") as wf:
            params = wf.getparams()
            raw_frames = wf.readframes(params.nframes)
        sampwidth = params.sampwidth
        n_channels = params.nchannels
        framerate = params.framerate or 44100
        dtype = np.int8 if sampwidth == 1 else (np.int16 if sampwidth == 2 else (np.int32 if sampwidth == 4 else None))
        if dtype is not None:
            audio = np.frombuffer(raw_frames, dtype=dtype)
            audio = audio.reshape((-1, n_channels)) if n_channels > 1 else audio.reshape((-1, 1))
            pitch_factor = random.uniform(0.9, 1.1)
            orig_len, new_len = audio.shape[0], max(1, int(audio.shape[0] / pitch_factor))
            new_idx = np.linspace(0, orig_len - 1, new_len)
            resampled = np.empty((new_len, audio.shape[1]), dtype=np.float32)
            for ch in range(audio.shape[1]):
                resampled[:, ch] = np.interp(new_idx, np.arange(orig_len), audio[:, ch].astype(np.float32))
            resampled_int = np.clip(resampled, np.iinfo(dtype).min, np.iinfo(dtype).max).astype(dtype)
            frames = resampled_int.tobytes()
            dur = new_len / float(framerate)
        else:
            frames, dur = raw_frames, params.nframes / float(framerate)
        if chosen_params is None:
            chosen_params = params
        else:
            if params.nchannels != chosen_params.nchannels or params.sampwidth != chosen_params.sampwidth or framerate != chosen_params.framerate:
                raise ValueError("All voice clips must have same channels, sample width and framerate.")
        combined_frames.extend(frames)
        start_time = current_time
        end_time = current_time + dur
        syllable_events.append({"token_index": idx, "start": start_time, "end": end_time})
        current_time = end_time
        if tok.strip().endswith((".", "!", "?")):
            append_silence(0.6)
            current_time += 0.6
    if chosen_params is None:
        raise RuntimeError("No voice clips were used.")
    with wave.open(out_file, "wb") as out_wf:
        out_wf.setparams(chosen_params)
        out_wf.writeframes(combined_frames)
    timing_path = os.path.splitext(out_file)[0] + "_timing.json"
    try:
        with open(timing_path, "w", encoding="utf-8") as f:
            json.dump({"tokens": tokens, "syllables": syllable_events}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return out_file


def post_to_discord(video_path: str, webhook_url: Optional[str] = None, content: Optional[str] = None) -> bool:
    """Post the video to Discord via webhook. Returns True on success."""
    webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Warning: No Discord webhook URL. Set DISCORD_WEBHOOK_URL or pass webhook_url.")
        return False
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        return False
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb > 25:
        print(f"Warning: Video is {size_mb:.1f}MB; Discord webhook limit is 25MB.")
        return False
    try:
        message = content if content is not None else f"🌙 Ramadan - {datetime.now().strftime('%d %B %Y')}"
        with open(video_path, "rb") as f:
            resp = requests.post(webhook_url, files={"file": (os.path.basename(video_path), f, "video/mp4")}, data={"content": message})
        resp.raise_for_status()
        print("✅ Video posted to Discord!")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Error posting to Discord: {e}")
        return False


# ---------- RAMADAN CONFIG ----------

RAMADAN_START_DATE = date(2025, 2, 17)
QURAN_API_BASE = "https://quranapi.pages.dev/api"
VOICE_CLIPS_DIR = os.path.join(BASE_DIR, "voice", "jeroen", "clips")
SLIDE_IMAGE = os.path.join(BASE_DIR, "slide.png")

# Background music: 01.mp3 to 08.mp3 in music/ (one picked at random)
RAMADAN_MUSIC_NAMES = [f"{i:02d}.mp3" for i in range(1, 9)]
VERSE_FONT_SIZE = 56
VERSE_REF_FONT_SIZE = 32

# Day vs night: 6:00–18:00 = day, 18:00–6:00 = night (for background choice)
DAY_HOUR_START = 6
DAY_HOUR_END = 18


# ---------- QURAN API ----------

def fetch_surah_list():
    """Fetch list of surahs (chapters) with totalAyah per surah."""
    url = f"{QURAN_API_BASE}/surah.json"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_random_verse():
    """
    Fetch a random verse from the Quran. Returns dict with arabic (with tashkeel),
    surahName, surahNameArabic, ayahNo, english.
    """
    surahs = fetch_surah_list()
    # Pick random surah (1-114); API returns list without surahNo, so use index + 1
    idx = random.randrange(len(surahs))
    surah = surahs[idx]
    surah_no = idx + 1
    total_ayah = surah["totalAyah"]
    ayah_no = random.randint(1, total_ayah)
    url = f"{QURAN_API_BASE}/{surah_no}/{ayah_no}.json"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    arabic = data.get("arabic1") or data.get("arabic2") or ""
    # Audio: first reciter's URL for verse recitation (so the quote is "read aloud")
    audio_url = None
    audio_data = data.get("audio")
    if isinstance(audio_data, dict) and audio_data:
        first_reciter = audio_data.get("1") or next(iter(audio_data.values()), None)
        if isinstance(first_reciter, dict) and first_reciter.get("url"):
            audio_url = first_reciter["url"]
    return {
        "arabic": arabic,
        "surahName": data.get("surahName", ""),
        "surahNameArabic": data.get("surahNameArabic", ""),
        "ayahNo": data.get("ayahNo", 0),
        "english": data.get("english", ""),
        "audio_url": audio_url,
    }


def _is_day_time(hour: int) -> bool:
    """True if hour is in the day range (DAY_HOUR_START inclusive to DAY_HOUR_END exclusive)."""
    return DAY_HOUR_START <= hour < DAY_HOUR_END


def pick_background_path() -> Optional[str]:
    """
    Pick a random background from video_parts/backgrounds based on time:
    - Day (6:00–19:59): random from bg_day_*.png
    - Night (20:00–5:59): random from bg_night_*.png
    Returns full path or None if no matching files.
    """
    hour = datetime.now().hour
    use_day = _is_day_time(hour)
    pattern = os.path.join(BACKGROUND_DIR, "bg_day_*.png") if use_day else os.path.join(BACKGROUND_DIR, "bg_night_*.png")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return random.choice(candidates)


def get_ramadan_music_path():
    """Return path to a random background music file (01.mp3–08.mp3 in music/)."""
    candidates = [
        os.path.join(MUSIC_DIR, name)
        for name in RAMADAN_MUSIC_NAMES
        if os.path.isfile(os.path.join(MUSIC_DIR, name))
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No Ramadan music found. Add 01.mp3–08.mp3 in {MUSIC_DIR}"
        )
    return random.choice(candidates)


def download_verse_audio(audio_url: str) -> str:
    """Download verse recitation MP3 to a temp file; return path. Caller should unlink when done."""
    resp = requests.get(audio_url, timeout=15)
    resp.raise_for_status()
    fd, path = tempfile.mkstemp(suffix=".mp3")
    try:
        os.write(fd, resp.content)
    finally:
        os.close(fd)
    return path


# ---------- RAMADAN INTRO TEXT (Dutch) ----------

RAMADAN_INTRO_PATTERNS = [
    "Vandaag een vers uit de Koran. Ramadan mubarak.",
    "Een vers voor vandaag. Ramadan mubarak.",
    "Ramadan mubarak. Hier een vers uit de Koran.",
]


def build_ramadan_intro_text():
    """Short Dutch intro line for the video (spoken with gibberish voice)."""
    return random.choice(RAMADAN_INTRO_PATTERNS)


# ---------- RAMADAN VIDEO ----------

def create_ramadan_video(
    voice_file: str,
    music_file: str,
    background_path: Optional[str],
    verse_arabic: str,
    verse_ref: str,
    verse_english: str,
    slide_img: str,
    out_file: str = "ramadan_vandaag.mp4",
    verse_audio_file: Optional[str] = None,
):
    """
    Build Ramadan video: blurred background (day/night PNG from background_path),
    Arabic verse overlay, optional avatar, Dutch intro as subtitles.
    If verse_audio_file is set, the verse is also read aloud (Quran recitation) after the intro.
    """
    voice = AudioFileClip(voice_file)
    music_base = AudioFileClip(music_file)

    avatar_entry_duration = 1.0
    verse_audio = None
    if verse_audio_file and os.path.isfile(verse_audio_file):
        verse_audio = AudioFileClip(verse_audio_file)
        total_duration = avatar_entry_duration + voice.duration + verse_audio.duration + EXTRA_TAIL_SECONDS
    else:
        total_duration = avatar_entry_duration + voice.duration + EXTRA_TAIL_SECONDS

    if music_base.duration >= total_duration:
        music = music_base.subclipped(0, total_duration)
    else:
        from moviepy.audio.AudioClip import concatenate_audioclips
        loops = int(total_duration // music_base.duration) + 1
        music_long = concatenate_audioclips([music_base] * loops)
        music = music_long.subclipped(0, total_duration)

    voice_delayed = voice.with_start(avatar_entry_duration)
    clips_for_audio = [music, voice_delayed]
    if verse_audio is not None:
        verse_start = avatar_entry_duration + voice.duration
        clips_for_audio.append(verse_audio.with_start(verse_start))
    final_audio = CompositeAudioClip(clips_for_audio)

    # --- Background: day/night PNG from background_path, or fallback slide ---
    if background_path and os.path.isfile(background_path) and background_path.lower().endswith((".png", ".jpg", ".jpeg")):
        original_img = Image.open(background_path).convert("RGB")
        blurred_img = original_img.filter(ImageFilter.GaussianBlur(radius=12))
        blurred_array = np.array(blurred_img).astype(np.uint8)
        bg_clip = ImageClip(blurred_array).with_duration(total_duration)
    else:
        if os.path.isfile(slide_img):
            original_img = Image.open(slide_img).convert("RGB")
        else:
            original_img = Image.new("RGB", (1920, 1080), (20, 30, 50))
        blurred_img = original_img.filter(ImageFilter.GaussianBlur(radius=12))
        blurred_array = np.array(blurred_img).astype(np.uint8)
        bg_clip = ImageClip(blurred_array).with_duration(total_duration)

    # --- Audio envelope for avatar bounce (intro + verse recitation when present) ---
    try:
        from moviepy.audio.AudioClip import concatenate_audioclips
        if verse_audio is not None:
            combined_voice = concatenate_audioclips([voice, verse_audio])
            audio_array = combined_voice.to_soundarray()
            combined_voice.close()
        else:
            audio_array = voice.to_soundarray()
        rms = np.sqrt((audio_array.astype(float) ** 2).mean(axis=1)) if audio_array.ndim == 2 else np.sqrt((audio_array.astype(float) ** 2))
        max_rms = float(rms.max()) if rms.size > 0 else 0.0
        env = (rms / max_rms) if max_rms > 0 else np.zeros_like(rms)
    except Exception:
        env = None

    # --- Avatar (optional) ---
    avatar_clip = None
    if os.path.isfile(AVATAR_IMAGE):
        avatar = ImageClip(AVATAR_IMAGE).resized((256, 256)).with_duration(total_duration)
        bg_height = bg_clip.h
        avatar_height = avatar.h
        base_y = bg_height - avatar_height - 40
        audio_env = env
        voice_dur = voice.duration + (verse_audio.duration if verse_audio else 0)

        def bounce_pos(t):
            x = 40
            if t < avatar_entry_duration:
                progress = t / avatar_entry_duration
                eased = 1 - (1 - progress) ** 5
                if progress > 0.8:
                    bounce_progress = (progress - 0.8) / 0.2
                    eased += 0.05 * math.sin(bounce_progress * math.pi)
                y = bg_height + (base_y - bg_height) * eased
            else:
                bounce_t = t - avatar_entry_duration
                if audio_env is not None and voice_dur > 0:
                    idx = min(len(audio_env) - 1, max(0, int((bounce_t / voice_dur) * (len(audio_env) - 1))))
                    level = float(audio_env[idx])
                    y = base_y - 35 * level
                else:
                    y = base_y - 10 * abs(math.sin(2 * math.pi * bounce_t / 0.8))
            return (x, y)

        avatar_clip = avatar.with_position(bounce_pos)

    # --- Subtitles for Dutch intro ---
    intro_text = None
    try:
        with open(os.path.splitext(voice_file)[0] + "_timing.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        intro_text = "".join(meta.get("tokens") or [])
    except Exception:
        pass
    if not intro_text:
        intro_text = build_ramadan_intro_text()

    subtitle_clips = []
    try:
        timing_path = os.path.splitext(voice_file)[0] + "_timing.json"
        tokens = None
        syllables = None
        if os.path.isfile(timing_path):
            with open(timing_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            tokens = meta.get("tokens")
            syllables = meta.get("syllables")
        if not tokens:
            tokens = split_into_syllable_tokens(intro_text)
        if not tokens:
            raise ValueError("Empty intro text")

        try:
            font = ImageFont.truetype(os.path.join(FONT_DIR, "Arial.ttf"), NORMAL_TEXT_FONT_SIZE)
        except IOError:
            font = ImageFont.load_default()

        box_width = int(bg_clip.w * 0.75)
        x_pos = 256 + 20 + 60

        if syllables:
            syllable_events = syllables
        else:
            syllable_indices = [i for i, tok in enumerate(tokens) if any(c.isalpha() for c in tok)]
            total_syl = len(syllable_indices)
            if total_syl == 0:
                raise ValueError("No syllable tokens")
            syllable_events = []
            for step, idx in enumerate(syllable_indices, start=1):
                start_t = (step - 1) / total_syl * voice.duration
                end_t = step / total_syl * voice.duration if step < total_syl else voice.duration
                syllable_events.append({"token_index": idx, "start": start_t, "end": end_t})

        total_events = len(syllable_events)
        first_word_index = None
        for i, ev in enumerate(syllable_events):
            idx = ev["token_index"]
            if idx < len(tokens) and any(c.isalpha() for c in tokens[idx]):
                first_word_index = i
                break
        if first_word_index is None and syllable_events:
            first_word_index = 0

        for i, ev in enumerate(syllable_events):
            idx = ev["token_index"]
            start_t = float(ev.get("start", 0))
            if first_word_index is not None and i < first_word_index:
                continue
            partial_text = "".join(tokens[: idx + 1])
            words = partial_text.split()
            lines = []
            current = ""
            for w in words:
                test = (current + " " + w).strip()
                if font.getlength(test) <= box_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    current = w
            if current:
                lines.append(current)
            line_height = int(font.size * 1.3)
            text_height = line_height * len(lines)
            pad_x, pad_y = 20, 12
            bubble_w = box_width + 2 * pad_x
            bubble_h = text_height + 2 * pad_y
            subtitle_img = Image.new("RGBA", (bubble_w, bubble_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(subtitle_img)
            try:
                draw.rounded_rectangle(
                    [(0, 0), (bubble_w - 1, bubble_h - 1)],
                    radius=18,
                    fill=(0, 0, 0, 180),
                    outline=(255, 255, 255, 220),
                    width=SUBTITLE_BORDER_WIDTH,
                )
            except AttributeError:
                draw.rectangle(
                    [(0, 0), (bubble_w - 1, bubble_h - 1)],
                    fill=(0, 0, 0, 180),
                    outline=(255, 255, 255, 220),
                    width=SUBTITLE_BORDER_WIDTH,
                )
            y_off = pad_y
            for line in lines:
                draw.text((pad_x, y_off), line, font=font, fill=(255, 255, 255, 255))
                y_off += line_height
            y_pos = bg_clip.h - bubble_h - 50
            if i < total_events - 1:
                next_start = float(syllable_events[i + 1].get("start", start_t))
                clip_duration = max(0.01, next_start - start_t)
            else:
                clip_duration = max(0.01, total_duration - start_t)
            subtitle_start_t = start_t + avatar_entry_duration
            sc = (
                ImageClip(np.array(subtitle_img))
                .with_duration(clip_duration)
                .with_start(subtitle_start_t)
                .with_position((x_pos, y_pos))
            )
            subtitle_clips.append(sc)
    except Exception:
        subtitle_clips = []

    # --- Arabic verse overlay (centered card) ---
    verse_overlay_clips = []
    try:
        # Prefer a font that supports Arabic; fallback to Arial
        try:
            verse_font = ImageFont.truetype(os.path.join(FONT_DIR, "Arial-Bold.ttf"), VERSE_FONT_SIZE)
            ref_font = ImageFont.truetype(os.path.join(FONT_DIR, "Arial.ttf"), VERSE_REF_FONT_SIZE)
        except IOError:
            verse_font = ImageFont.load_default()
            ref_font = ImageFont.load_default()

        max_verse_width = int(bg_clip.w * 0.85)
        # Wrap Arabic verse by word (spaces) so we don't break words
        verse_lines = []
        parts = verse_arabic.split() if verse_arabic else []
        current = ""
        for part in parts:
            test = (current + " " + part).strip() if current else part
            if verse_font.getlength(test) <= max_verse_width:
                current = test
            else:
                if current:
                    verse_lines.append(current)
                current = part
        if current:
            verse_lines.append(current)
        if not verse_lines and verse_arabic:
            verse_lines = [verse_arabic]

        # Wrap English translation for subtitle-style lines under the Arabic
        translation_lines: list[str] = []
        if verse_english:
            words = verse_english.split()
            current_tr = ""
            for w in words:
                test = (current_tr + " " + w).strip() if current_tr else w
                if ref_font.getlength(test) <= max_verse_width:
                    current_tr = test
                else:
                    if current_tr:
                        translation_lines.append(current_tr)
                    current_tr = w
            if current_tr:
                translation_lines.append(current_tr)

        arabic_line_height = int(verse_font.size * 1.4)
        translation_line_height = int(ref_font.size * 1.3)
        ref_h = int(ref_font.size * 1.2)
        pad = 40
        box_w = min(max_verse_width + 2 * pad, bg_clip.w - 80)
        box_h = (
            len(verse_lines) * arabic_line_height
            + (len(translation_lines) * translation_line_height if translation_lines else 0)
            + ref_h
            + 4 * pad
        )

        bg_box = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 200))
        draw_box = ImageDraw.Draw(bg_box)
        draw_box.rectangle(
            [(0, 0), (box_w - 1, box_h - 1)],
            outline=(211, 175, 55, 255),
            width=2,
        )

        verse_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        draw_v = ImageDraw.Draw(verse_img)
        y_off = pad

        # Arabic lines (top of card)
        for line in verse_lines:
            # Center each line (Arabic is RTL; PIL handles it with the right font)
            lw = verse_font.getlength(line)
            draw_v.text(((box_w - lw) // 2, y_off), line, font=verse_font, fill=(255, 255, 255, 255))
            y_off += arabic_line_height

        # Small gap, then English translation lines (subtitles under Arabic)
        if translation_lines:
            y_off += pad // 2
            for line in translation_lines:
                lw = ref_font.getlength(line)
                draw_v.text(
                    ((box_w - lw) // 2, y_off),
                    line,
                    font=ref_font,
                    fill=(230, 230, 230, 255),
                )
                y_off += translation_line_height

        # Gap, then reference line at the bottom
        y_off += pad // 2
        ref_w = ref_font.getlength(verse_ref)
        draw_v.text(((box_w - ref_w) // 2, y_off), verse_ref, font=ref_font, fill=(255, 255, 255, 200))

        overlay_x = (bg_clip.w - box_w) // 2
        overlay_y = (bg_clip.h - box_h) // 2 - 50
        # When verse is read aloud, show the verse card when recitation starts
        if verse_audio is not None:
            fade_start = avatar_entry_duration + voice.duration + 0.3
        else:
            fade_start = avatar_entry_duration + 0.5
        fade_duration = 0.6
        visible_duration = max(0.01, total_duration - fade_start)
        crossfade = CrossFadeIn(duration=fade_duration)
        bg_arr = np.array(bg_box).astype(np.uint8)
        bg_layer = ImageClip(bg_arr).with_duration(visible_duration)
        try:
            bg_layer = crossfade.apply(bg_layer)
        except Exception:
            pass
        bg_layer = bg_layer.with_start(fade_start).with_position((overlay_x, overlay_y))
        verse_arr = np.array(verse_img).astype(np.uint8)
        verse_layer = ImageClip(verse_arr).with_duration(visible_duration)
        try:
            verse_layer = crossfade.apply(verse_layer)
        except Exception:
            pass
        verse_layer = verse_layer.with_start(fade_start + 0.05).with_position((overlay_x, overlay_y))
        verse_overlay_clips = [bg_layer, verse_layer]
    except Exception as e:
        print(f"Warning: Could not create verse overlay: {e}")
        verse_overlay_clips = []

    # --- Composite ---
    video_layers = [bg_clip]
    if avatar_clip is not None:
        video_layers.append(avatar_clip)
    video_layers.extend(verse_overlay_clips)
    video_layers.extend(subtitle_clips)

    final_video = CompositeVideoClip(video_layers)
    final_video = final_video.with_audio(final_audio)

    fps = 24
    frames_to_cut = 8
    cut_duration = frames_to_cut / fps
    if total_duration > cut_duration:
        new_end = total_duration - cut_duration
        if new_end > 0:
            try:
                final_video = final_video.subclipped(0, new_end)
            except Exception:
                pass

    final_video.write_videofile(out_file, fps=fps, codec="libx264", audio_codec="aac")

    voice.close()
    music_base.close()
    if verse_audio is not None:
        verse_audio.close()
    final_video.close()


# ---------- MAIN ----------

def main(post_to_discord_enabled=True):
    today = date.today()
    if today < RAMADAN_START_DATE:
        print(f"Ramadan starts on {RAMADAN_START_DATE}. Not running before then.")
        return

    print("Fetching random verse from Quran API...")
    verse = get_random_verse()
    verse_arabic = verse["arabic"]
    verse_ref = f"{verse['surahName']} {verse['ayahNo']}"
    print(f"Verse: {verse_ref} — {verse['english'][:60]}...")

    verse_audio_path = None
    if verse.get("audio_url"):
        print("Downloading verse recitation (so the quote is read aloud)...")
        try:
            verse_audio_path = download_verse_audio(verse["audio_url"])
        except Exception as e:
            print(f"Warning: Could not download verse audio: {e}")

    background_path = pick_background_path()
    time_of_day = "day" if _is_day_time(datetime.now().hour) else "night"
    bg_label = os.path.basename(background_path) if background_path else "fallback slide"
    print(f"Background: {time_of_day} — {bg_label}")

    intro_text = build_ramadan_intro_text()
    print("Intro text:", intro_text)
    print("Generating gibberish voice...")
    voice_file = create_gibberish_voice(intro_text, voices_dir=VOICE_CLIPS_DIR, out_file=os.path.join(BASE_DIR, "ramadan_voice.wav"))

    music_file = get_ramadan_music_path()
    slide_img = SLIDE_IMAGE

    print("Rendering Ramadan video...")
    video_path = os.path.join(BASE_DIR, "ramadan_vandaag.mp4")
    try:
        create_ramadan_video(
            voice_file=voice_file,
            music_file=music_file,
            background_path=background_path,
            verse_arabic=verse_arabic,
            verse_ref=verse_ref,
            verse_english=verse["english"],
            slide_img=slide_img,
            out_file=video_path,
            verse_audio_file=verse_audio_path,
        )
    finally:
        if verse_audio_path and os.path.isfile(verse_audio_path):
            try:
                os.unlink(verse_audio_path)
            except Exception:
                pass
    print("Done! Video saved as", video_path)

    if post_to_discord_enabled:
        webhook = os.environ.get("RAMADAN_DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")
        message = f"🌙 Ramadan - vers van de dag - {datetime.now().strftime('%d %B %Y')}"
        post_to_discord(video_path, webhook_url=webhook, content=message)


if __name__ == "__main__":
    import sys
    post_enabled = "--no-discord" not in sys.argv
    main(post_to_discord_enabled=post_enabled)

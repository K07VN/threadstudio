"""AI & deterministic Vietnamese teencode normalizer + Gemini Vision OCR."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import urllib.request

from ..core.models import SegmentItem

# High-frequency Vietnamese Threads/TikTok slang & abbreviations
_VI_TEENCODE_MAP: dict[str, str] = {
    "ko": "không",
    "k": "không",
    "kh": "không",
    "khum": "không",
    "hong": "không",
    "hông": "không",
    "hok": "không",
    "đc": "được",
    "dc": "được",
    "dk": "được",
    "đk": "được",
    "nc": "nước",
    "nnay": "như này",
    "ntn": "như thế nào",
    "trc": "trước",
    "bh": "bây giờ",
    "h": "giờ",
    "mn": "mọi người",
    "mng": "mọi người",
    "mik": "mình",
    "mk": "mình",
    "mh": "mình",
    "tui": "tôi",
    "t": "tôi",
    "m": "mày",
    "b": "bạn",
    "bn": "bạn",
    "vs": "với",
    "v": "vậy",
    "z": "vậy",
    "zậy": "vậy",
    "cx": "cũng",
    "cug": "cũng",
    "bt": "bình thường",
    "bth": "bình thường",
    "iu": "yêu",
    "thik": "thích",
    "thik": "thích",
    "r": "rồi",
    "rùi": "rồi",
    "lun": "luôn",
    "luon": "luôn",
    "ng": "người",
    "ngta": "người ta",
    "nta": "người ta",
    "sp": "sản phẩm",
    "ny": "người yêu",
    "nyc": "người yêu cũ",
    "gd": "gia đình",
    "gđ": "gia đình",
    "vn": "Việt Nam",
    "tq": "Trung Quốc",
    "ae": "anh em",
    "ce": "chị em",
    "ib": "nhắn tin",
    "cmt": "bình luận",
    "rep": "trả lời",
    "acc": "tài khoản",
    "ad": "admin",
    "đhs": "chẳng hiểu sao",
    "vcl": "vãi chưởng",
    "vl": "vãi luôn",
    "đm": "đậu xanh",
    "oke": "ok",
    "okela": "ok luôn",
    "j": "gì",
    "gi": "gì",
    "bik": "biết",
    "bit": "biết",
    "chx": "chưa",
    "ms": "mới",
    "lm": "làm",
    "ns": "nói",
    "nv": "như vậy",
    "kb": "kết bạn",
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMOTICON_RE = re.compile(r"[:=;8][\-^']?[)DdpP(\[\]/\\|3]+|\)+|=\)+|:\(+|<3+")
_REPEAT_CHAR_RE = re.compile(r"([a-zA-Zà-ỹÀ-Ỹ])\1{2,}")
_MULTISPACE_RE = re.compile(r"\s+")


def normalize_teencode_offline(text: str, *, allow_emotion_tags: bool = False) -> str:
    """Deterministic Vietnamese teencode expansion (~90% coverage without API keys)."""
    if not text:
        return ""
    cleaned = _URL_RE.sub("", text)
    if allow_emotion_tags and re.search(r"=?\){3,}|haha|hihi|kaka", cleaned, re.IGNORECASE):
        cleaned = _EMOTICON_RE.sub(" [cười] ", cleaned)
    else:
        cleaned = _EMOTICON_RE.sub(" ", cleaned)

    # Collapse elongated endings like "điiiii" -> "đi"
    cleaned = _REPEAT_CHAR_RE.sub(r"\1", cleaned)

    # Replace whole tokens preserving punctuation
    def _replace_word(match: re.Match[str]) -> str:
        word = match.group(0)
        low = word.lower()
        if low in _VI_TEENCODE_MAP:
            repl = _VI_TEENCODE_MAP[low]
            if word[0].isupper() and repl:
                return repl[0].upper() + repl[1:]
            return repl
        return word

    cleaned = re.sub(r"\b[a-zA-Z0-9à-ỹÀ-ỸđĐ]+\b", _replace_word, cleaned)
    cleaned = re.sub(r"[^\w\s,.!?;:'\"“”\-()\[\]à-ỹÀ-ỸđĐ]", " ", cleaned)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned).strip()
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def normalize_segments_batch(
    segments: list[SegmentItem],
    *,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash",
    include_emotion_cues: bool = False,
) -> dict[str, str]:
    """Normalize a full list of Thread segments via Gemini (with image OCR) + offline fallback."""
    offline_map: dict[str, str] = {}
    for seg in segments:
        base = normalize_teencode_offline(seg.raw_text, allow_emotion_tags=include_emotion_cues)
        offline_map[seg.seg_id] = base or seg.raw_text.strip()

    for seg in segments:
        seg.spoken_text = offline_map[seg.seg_id]

    if not gemini_api_key.strip() or not segments:
        return offline_map

    try:
        gemini_map = _call_gemini_batch(
            segments,
            api_key=gemini_api_key.strip(),
            model=gemini_model.strip() or "gemini-2.5-flash",
            include_emotion_cues=include_emotion_cues,
        )
        for seg in segments:
            if seg.seg_id in gemini_map and gemini_map[seg.seg_id].strip():
                seg.spoken_text = gemini_map[seg.seg_id].strip()
                offline_map[seg.seg_id] = seg.spoken_text
    except Exception:
        # Offline normalization is already populated on every segment
        pass

    return offline_map


def _call_gemini_batch(
    segments: list[SegmentItem],
    *,
    api_key: str,
    model: str,
    include_emotion_cues: bool,
) -> dict[str, str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    emotion_rule = (
        "Nếu câu có tiếng cười lớn (=))), haha) có thể chèn [cười], thở dài chèn [thở dài]."
        if include_emotion_cues
        else "Bỏ toàn bộ ký hiệu mặt cười (:))), =))), emoji) để máy đọc TTS tự nhiên nhất."
    )
    prompt = (
        "Bạn là biên tập viên lời đọc TTS tiếng Việt chuyên nghiệp cho video đọc bình luận Threads.\n"
        "Nhiệm vụ:\n"
        "1. Bung toàn bộ từ viết tắt, teencode, tiếng lóng sang tiếng Việt chuẩn tự nhiên để đọc thành tiếng "
        "(ví dụ: ko->không, đc->được, nc->nước, nnay->như này, pic cờ bôn->pickleball...).\n"
        "2. Giữ nguyên giọng điệu đời thường của người viết, KHÔNG tóm tắt, KHÔNG bịa thêm ý.\n"
        "3. Nếu đoạn có đính kèm ảnh chụp màn hình đoạn chat/meme mà chữ bài viết ngắn, hãy nối thêm phần đọc ngắn gọn nội dung chữ quan trọng trong ảnh.\n"
        f"4. {emotion_rule}\n"
        "Trả về DUY NHẤT một JSON object dạng {\"<seg_id>\": \"<lời đọc chuẩn>\"}."
    )

    items_payload = [{"seg_id": s.seg_id, "raw_text": s.raw_text} for s in segments]
    parts: list[dict] = [{"text": prompt + "\n\nDữ liệu:\n" + json.dumps(items_payload, ensure_ascii=False)}]

    # Attach root post image if present so Gemini Vision can transcribe chat memes like Video 2 (02:20)
    for seg in segments[:2]:
        for img_path_str in seg.image_locals[:1]:
            p = Path(img_path_str)
            if p.exists() and p.stat().st_size < 3_500_000:
                mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                parts.append({"text": f"Ảnh đính kèm của đoạn {seg.seg_id}:"})
                parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    cand_text = raw["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(cand_text)
    if isinstance(parsed, dict):
        return {str(k): str(v) for k, v in parsed.items()}
    return {}


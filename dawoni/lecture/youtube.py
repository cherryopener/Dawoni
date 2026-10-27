"""유튜브 링크 → 자막·제목. 자막은 youtube-transcript-api, 제목은 oEmbed (API 키 불필요).

영상·음성은 내려받지 않는다. 자막이 없는 영상은 아직 지원하지 않는다 (안내만).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from dawoni.models.lecture import CaptionLine, LectureSource

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PATTERNS = (re.compile(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})"),)


class LectureError(Exception):
    """강사에게 보여 줄 안내."""


def video_id_of(url: str) -> str:
    s = url.strip()
    if _ID.match(s):
        return s
    for p in _PATTERNS:
        m = p.search(s)
        if m:
            return m.group(1)
    raise LectureError(f"유튜브 영상 주소가 아닙니다: {url}")


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def fetch_meta(video_id: str, timeout: float = 15) -> tuple[str, str | None]:
    q = urllib.parse.urlencode({"url": canonical_url(video_id), "format": "json"})
    try:
        with urllib.request.urlopen(f"https://www.youtube.com/oembed?{q}", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError, TimeoutError, json.JSONDecodeError:
        return video_id, None
    return str(data.get("title") or video_id), data.get("author_name")


def fetch_captions(
    video_id: str, languages: tuple[str, ...] = ("ko", "en")
) -> tuple[list[CaptionLine], str, bool]:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (  # pyright: ignore[reportPrivateUsage]
        CouldNotRetrieveTranscript,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    try:
        t = YouTubeTranscriptApi().fetch(video_id, languages=list(languages))
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        raise LectureError("이 영상에는 자막(자동 생성 포함)이 없어 요약할 수 없습니다.") from e
    except VideoUnavailable as e:
        raise LectureError("영상을 볼 수 없습니다 (비공개·삭제·지역 제한).") from e
    except CouldNotRetrieveTranscript as e:
        raise LectureError(f"자막을 가져오지 못했습니다: {str(e).splitlines()[0][:200]}") from e
    lines = [
        CaptionLine(start=float(s.start), text=s.text.replace("\n", " ").strip())
        for s in t.snippets
        if s.text.strip()
    ]
    if not lines:
        raise LectureError("자막이 비어 있습니다.")
    return lines, t.language_code, bool(t.is_generated)


Fetcher = Callable[[str], tuple[list[CaptionLine], str, bool]]


def load_lecture(
    url: str, *, fetcher: Fetcher | None = None, with_meta: bool = True
) -> LectureSource:
    vid = video_id_of(url)
    lines, lang, generated = (fetcher or fetch_captions)(vid)
    title, channel = fetch_meta(vid) if with_meta else (vid, None)
    last = lines[-1]
    return LectureSource(
        video_id=vid, url=canonical_url(vid), title=title, channel=channel, language=lang,
        generated=generated, duration_s=last.start + 5, lines=lines,
    )  # fmt: skip

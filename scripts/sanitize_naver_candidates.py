#!/usr/bin/env python3
"""Run candidate sanitation with baby-product subtype vocabulary expanded."""
from __future__ import annotations

import sanitize_discovered_candidates as sanitizer

sanitizer.DIRECT_HINTS = tuple(dict.fromkeys(sanitizer.DIRECT_HINTS + ("/catalog/",)))
sanitizer.CATEGORY_TERMS = {
    "완구": (
        "완구", "장난감", "딸랑이", "모빌", "촉감", "래틀", "놀이",
        "인형", "볼풀", "에듀볼", "원목", "블록", "아기체육관",
    ),
    "구강·치발기": (
        "치발기", "구강", "잇몸", "티더", "teether", "과즙망", "구강발달",
    ),
    "턱받이": ("턱받이", "빕", "bib"),
    "수유용품": (
        "수유", "젖병", "분유", "빨대컵", "수유쿠션", "유축", "모유",
        "수유패드", "젖병건조대", "분유케이스", "물병",
    ),
    "이유식·식기": (
        "이유식", "흡착식판", "스푼", "숟가락", "유아식기", "아기식기",
        "이유식기", "식판", "큐브", "보관용기", "조리도구", "도마",
    ),
    "위생·기저귀": (
        "위생", "물티슈", "기저귀", "손수건", "목욕", "세정", "면봉",
        "타월", "욕조", "샴푸캡", "목욕장갑", "수건",
    ),
}

sanitizer.main()

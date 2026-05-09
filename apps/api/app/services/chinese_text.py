from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# jieba lazy initialisation
# ---------------------------------------------------------------------------
_jieba_cut: Callable[[str], list[str]] | None = None


def _get_jieba_cut() -> Callable[[str], list[str]]:
    """Lazy-load jieba with module-level caching to avoid repeated dictionary loading."""
    global _jieba_cut
    if _jieba_cut is None:
        import jieba

        _jieba_cut = lambda text: jieba.lcut(text, cut_all=False, HMM=True)
    return _jieba_cut


# ---------------------------------------------------------------------------
# Stop words (minimal, high-frequency Chinese words)
# ---------------------------------------------------------------------------
_STOP_WORDS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "这些", "那些", "这个", "那个", "之", "与", "及", "等",
    "对", "可以", "它", "他", "她", "我们", "你们", "他们", "它们", "或", "但是",
    "而", "因为", "所以", "如果", "即使", "虽然", "关于", "对于", "以及", "还是",
    "并且", "不过", "只是", "这样", "那样", "这里", "那里", "哪里", "什么", "怎么",
    "为什么", "如何", "谁", "哪", "个", "种", "者", "家", "员", "性", "化", "学",
    "中", "大", "小", "多", "少", "高", "低", "长", "短", "来", "过", "下", "前",
    "后", "内", "外", "里", "间", "边", "面", "头", "部", "身", "心", "手", "眼",
    "口", "声", "地", "得", "着", "过", "但", "为", "被", "把", "让", "向", "从",
    "比", "当", "可", "能", "还", "将", "并", "已", "以", "及", "但", "只", "最",
    "更", "太", "非常", "已经", "正在", "曾经", "现在", "以后", "然后", "接着",
    "首先", "其次", "最后", "总之", "例如", "比如", "像", "如", "即", "便", "即使",
    "尽管", "不管", "无论", "不仅", "不但", "而且", "并且", "或者", "要么", "既",
    "又", "也", "还", "再", "才", "就", "都", "全", "总", "共", "同", "另", "别",
    "其", "其中", "其余", "其他", "另外", "此外", "除此之外", "另一方面", "一方面",
    "一直", "一向", "从来", "始终", "永远", "暂时", "临时", "偶然", "突然", "忽然",
    "渐渐", "逐渐", "逐步", "不断", "继续", "持续", "保持", "维持", "坚持", "坚定",
    "坚决", "坚强", "顽强", "固执", "顽固",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def contains_chinese(text: str) -> bool:
    """Return True if *text* contains any CJK Unified Ideograph."""
    return bool(_RE_CJK.search(text))


@lru_cache(maxsize=1024)
def tokenize_for_retrieval(text: str) -> list[str]:
    """Unified retrieval tokenisation.

    * English: words, numbers, Greek letters, math operators.
    * Chinese: jieba exact-mode segmentation.
    * Single CJK characters are kept (some technical terms are single-char).
    * Single non-CJK characters are dropped unless they are operators.
    """
    if not text:
        return []

    tokens: list[str] = []
    lower = text.lower()

    for match in _RE_TOKEN.finditer(lower):
        token = match.group(0)
        if _RE_ONLY_CJK.fullmatch(token):
            tokens.extend(_get_jieba_cut()(token))
        else:
            if len(token) > 1 or _RE_OPERATOR.fullmatch(token):
                tokens.append(token)

    return tokens


@lru_cache(maxsize=1024)
def extract_terms(text: str) -> set[str]:
    """Extract a set of likely *terms* from *text* for Agent use.

    * English: tokens longer than 2 characters.
    * Chinese: jieba tokens of length >= 2 that are not stop-words.
    """
    if not text:
        return set()

    terms: set[str] = set()
    lower = text.lower()

    for match in _RE_TOKEN.finditer(lower):
        token = match.group(0)
        if _RE_ONLY_CJK.fullmatch(token):
            for word in _get_jieba_cut()(token):
                if len(word) >= 2 and word not in _STOP_WORDS:
                    terms.add(word)
        else:
            if len(token) > 2:
                terms.add(token)

    return terms


def split_sentences(text: str) -> list[str]:
    """Split *text* into sentences, supporting both Chinese and English punctuation."""
    normalized = text.replace("\r\n", "\n")
    raw = _RE_SENTENCE_SPLIT.split(normalized)
    sentences = [part.strip() for part in raw if part.strip()]
    return sentences if sentences else [text.strip()]


def estimate_tokens(text: str) -> int:
    """Estimate the number of sub-word tokens an LLM tokenizer would produce.

    * Chinese -> ~0.6 tokens per character (typical BPE).
    * English -> 1.0 token per word.
    """
    if not text:
        return 0

    total = 0
    for token in _RE_TOKEN_GLOB.findall(text.lower()):
        if _RE_ONLY_CJK.fullmatch(token):
            total += max(1, round(len(token) * 0.6))
        else:
            total += 1
    return max(1, total)


def split_multi_hop_query(question: str) -> list[str]:
    """Split a composite question into sub-queries.

    Supports English separators (and, ;, ,) as well as Chinese separators
    (和, 与, 、, ；, ，).
    """
    pattern = r"\band\b|[;，、；]|\b和\b|\b与\b"
    parts = [part.strip(" ,.;，、；") for part in re.split(pattern, question) if part.strip(" ,.;，、；")]
    if len(parts) >= 2:
        return parts[:3]
    return [question, f"background for {question}", f"relationships in {question}"]


# ---------------------------------------------------------------------------
# Compiled regexes (module level)
# ---------------------------------------------------------------------------
_RE_CJK = re.compile(r"[\u4e00-\u9fff]")
_RE_ONLY_CJK = re.compile(r"^[\u4e00-\u9fff]+$")
_RE_OPERATOR = re.compile(r"^[=<>+\-*/^()]+$")

_RE_TOKEN = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_\-]*|[0-9]+(?:\.[0-9]+)?|[α-ωΑ-Ω]+|[\u4e00-\u9fff]+|[=<>+\-*/^()]+"
)

_RE_TOKEN_GLOB = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_\-]*|[0-9]+(?:\.[0-9]+)?|[α-ωΑ-Ω]+|[\u4e00-\u9fff]+"
)

_RE_SENTENCE_SPLIT = re.compile(r"(?<=[。！？])|(?<=[.?!])\s+")

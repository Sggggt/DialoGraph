from __future__ import annotations

import pytest

from app.services.chinese_text import (
    contains_chinese,
    estimate_tokens,
    extract_terms,
    split_multi_hop_query,
    split_sentences,
    tokenize_for_retrieval,
)
from app.services.retrieval import classify_query_type


# ---------------------------------------------------------------------------
# tokenize_for_retrieval
# ---------------------------------------------------------------------------

def test_tokenize_chinese_words():
    """中文应按词切分，而非单字。"""
    tokens = tokenize_for_retrieval("知识图谱系统")
    # jieba should segment into at least multi-character words
    # Because "知识图谱" is in the custom dictionary it may appear as one token.
    assert any("知识" in t for t in tokens)
    assert any("图谱" in t for t in tokens)
    assert "系统" in tokens
    # Single-character tokens may appear, but we should have fewer of them
    single_chars = [t for t in tokens if len(t) == 1]
    assert len(single_chars) < len(tokens)


def test_tokenize_mixed_chinese_english():
    """混合文本应同时保留英文词和中文词。"""
    tokens = tokenize_for_retrieval("Degree centrality 度中心性")
    assert "degree" in tokens
    assert "centrality" in tokens
    # Without custom dictionary jieba segments into single/multi-character words
    assert "度" in tokens
    assert "中心" in tokens


def test_tokenize_keeps_math_operators():
    """数学运算符应被保留为 token。"""
    tokens = tokenize_for_retrieval("C = n - 1")
    assert "=" in tokens
    assert "-" in tokens


def test_tokenize_empty_and_whitespace():
    """空字符串和纯空格应返回空列表。"""
    assert tokenize_for_retrieval("") == []
    assert tokenize_for_retrieval("   ") == []


# ---------------------------------------------------------------------------
# extract_terms
# ---------------------------------------------------------------------------

def test_extract_terms_filters_stop_words():
    """停用词应被过滤。"""
    terms = extract_terms("什么是度中心性")
    assert "什么" not in terms
    assert "是" not in terms
    # Without custom dictionary jieba gives 度/中心/性; 度 is len 1 dropped,
    # 中心 (len 2) and 性 (len 1) -> 中心 survives
    assert "中心" in terms


def test_extract_terms_english():
    """英文术语提取规则：长度 > 2。"""
    terms = extract_terms("Degree centrality counts edges")
    assert "degree" in terms
    assert "centrality" in terms
    assert "counts" in terms
    assert "edges" in terms
    # Short words are dropped
    assert "a" not in terms
    assert "is" not in terms


def test_extract_terms_mixed():
    """混合文本应同时提取中英文术语。"""
    terms = extract_terms("Bayesian network 贝叶斯网络模型")
    assert "bayesian" in terms
    assert "network" in terms
    # "贝叶斯网络" may be kept as one term because it is in the custom dictionary.
    assert any("贝叶斯" in t for t in terms)
    assert any("网络" in t or "模型" in t for t in terms)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_chinese():
    """中文 token 估算不应被严重低估。"""
    text = "度中心性是图论中一个重要的概念，它衡量了节点在网络中的连接程度。"
    estimated = estimate_tokens(text)
    # The old heuristic (len(text.split())) would give ~1 because there are
    # no spaces.  The new heuristic should give roughly 0.6 * len(text).
    char_count = len(text)
    assert estimated > char_count * 0.4
    assert estimated <= char_count  # should not exceed character count


def test_estimate_tokens_english():
    """英文估算应与空格分词数接近。"""
    text = "Degree centrality counts incident edges for a node."
    estimated = estimate_tokens(text)
    word_count = len(text.split())
    assert estimated >= word_count * 0.8
    assert estimated <= word_count * 1.5


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# split_sentences
# ---------------------------------------------------------------------------

def test_split_sentences_chinese():
    """中文标点应正确切分句子。"""
    text = "这是第一句。这是第二句！这是第三句？"
    sentences = split_sentences(text)
    assert len(sentences) == 3
    assert "第一句" in sentences[0]
    assert "第二句" in sentences[1]
    assert "第三句" in sentences[2]


def test_split_sentences_english():
    """英文标点应正确切分句子。"""
    text = "First sentence. Second sentence! Third sentence?"
    sentences = split_sentences(text)
    assert len(sentences) == 3


def test_split_sentences_mixed():
    """混合中英文标点应正确切分。"""
    text = "Degree centrality counts edges. 度中心性是图论概念。"
    sentences = split_sentences(text)
    assert len(sentences) == 2


# ---------------------------------------------------------------------------
# split_multi_hop_query
# ---------------------------------------------------------------------------

def test_split_multi_hop_chinese():
    """中文并列查询应被正确拆分。"""
    parts = split_multi_hop_query("比较度中心性和中介中心性的区别")
    assert len(parts) >= 2
    assert any("度中心性" in p for p in parts)
    assert any("中介中心性" in p for p in parts)


def test_split_multi_hop_chinese_punctuation():
    """中文顿号、逗号应作为分隔符。"""
    parts = split_multi_hop_query("分析图、树、网络的特点")
    assert len(parts) >= 2
    assert any("图" in p for p in parts)
    assert any("树" in p for p in parts)


def test_split_multi_hop_english_fallback():
    """英文 and/; 仍应工作。"""
    parts = split_multi_hop_query("Compare degree and betweenness centrality")
    assert len(parts) >= 2


# ---------------------------------------------------------------------------
# classify_query_type (retrieval.py)
# ---------------------------------------------------------------------------

def test_classify_query_type_chinese_definition():
    assert classify_query_type("什么是度中心性") == "definition"
    assert classify_query_type("贝叶斯网络的定义") == "definition"
    assert classify_query_type("图论概念") == "definition"


def test_classify_query_type_chinese_formula():
    assert classify_query_type("贝叶斯定理的公式") == "formula"
    assert classify_query_type("证明中心极限定理") == "formula"


def test_classify_query_type_chinese_comparison():
    assert classify_query_type("比较度和中介中心性的区别") == "comparison"
    assert classify_query_type("两者之间的关系") == "comparison"


def test_classify_query_type_chinese_procedure():
    assert classify_query_type("Dijkstra算法的步骤") == "procedure"
    assert classify_query_type("如何计算pagerank") == "procedure"


# ---------------------------------------------------------------------------
# Agent graph terms replacement
# ---------------------------------------------------------------------------

def test_agent_terms_supports_chinese():
    """agent_graph._terms 替换后应能提取中文术语。"""
    from app.services.agent_graph import _terms

    terms = _terms("比较度中心性和中介中心性")
    # Without custom dictionary: 比较/度/中心/性/和/中介/中心/性
    # Stop words removed, single chars dropped -> 比较, 中心, 中介
    assert len(terms) >= 2
    assert "中心" in terms


def test_agent_terms_english_unchanged():
    """英文术语提取行为应保持不变。"""
    from app.services.agent_graph import _terms

    terms = _terms("degree centrality and betweenness")
    assert "degree" in terms
    assert "centrality" in terms
    assert "betweenness" in terms


# ---------------------------------------------------------------------------
# contains_chinese
# ---------------------------------------------------------------------------

def test_contains_chinese_true():
    assert contains_chinese("中文")
    assert contains_chinese("mixed 中文 text")


def test_contains_chinese_false():
    assert not contains_chinese("English only")
    assert not contains_chinese("")
    assert not contains_chinese("123 $#@")

from __future__ import annotations

import re

from session_doctor.schemas import Message, MessageFeature, NormalizedRole

from .feature_factories import message_feature
from .feature_models import RequestSignature

REPEAT_REQUEST_SIMILARITY_THRESHOLD = 0.35
EXACT_NORMALIZED_TEXT_BOOST = 0.10
MINIMUM_COMPARABLE_TOKEN_COUNT = 4

STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "be",
    "can",
    "could",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "please",
    "should",
    "that",
    "the",
    "these",
    "this",
    "to",
    "too",
    "we",
    "what",
    "with",
    "would",
    "you",
}

SYNONYMS = {
    "decided": "decision",
    "decides": "decision",
    "doc": "plan",
    "docs": "plan",
    "document": "plan",
    "failed": "fail",
    "failing": "fail",
    "failure": "fail",
    "fixed": "fix",
    "fixes": "fix",
    "parsed": "parse",
    "parsing": "parse",
    "tests": "test",
    "warnings": "warning",
}


def repeated_request_features(
    messages: list[Message],
    analysis_run_id: str,
) -> list[MessageFeature]:
    features: list[MessageFeature] = []
    previous_user_messages: list[tuple[Message, RequestSignature]] = []
    for message in messages:
        if message.role != NormalizedRole.USER or not message.text:
            continue
        signature = request_signature(message.text)
        if len(signature.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT:
            previous_user_messages.append((message, signature))
            continue

        best_match: tuple[Message, float] | None = None
        for previous_message, previous_signature in previous_user_messages:
            score = signature_similarity(signature, previous_signature)
            if best_match is None or score > best_match[1]:
                best_match = (previous_message, score)

        if best_match and best_match[1] >= REPEAT_REQUEST_SIMILARITY_THRESHOLD:
            matched_message, score = best_match
            features.append(
                message_feature(
                    analysis_run_id=analysis_run_id,
                    message=message,
                    feature_name="repeat_request_similarity",
                    feature_value=f"{score:.3f}",
                    score=score,
                    evidence={
                        "matched_message_id": matched_message.message_id,
                        "matched_source_event_id": matched_message.source_event_id,
                        "similarity_score": round(score, 3),
                        "threshold": REPEAT_REQUEST_SIMILARITY_THRESHOLD,
                    },
                )
            )

        previous_user_messages.append((message, signature))
    return features


def request_similarity(first: str, second: str) -> float:
    return signature_similarity(request_signature(first), request_signature(second))


def request_signature(text: str) -> RequestSignature:
    normalized = normalize_request_text(text)
    tokens = tuple(
        canonical_token(token)
        for token in normalized.split()
        if len(token) >= 2 and token not in STOPWORDS
    )
    compact_text = "".join(tokens)
    return RequestSignature(
        normalized_text=" ".join(tokens),
        tokens=tokens,
        token_set=frozenset(tokens),
        bigrams=frozenset(zip(tokens, tokens[1:], strict=False)),
        char_grams=char_grams(compact_text),
    )


def signature_similarity(first: RequestSignature, second: RequestSignature) -> float:
    if (
        len(first.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT
        or len(second.tokens) < MINIMUM_COMPARABLE_TOKEN_COUNT
    ):
        return 0.0
    score = (
        0.45 * jaccard(first.token_set, second.token_set)
        + 0.25 * jaccard(first.bigrams, second.bigrams)
        + 0.10 * jaccard(first.char_grams, second.char_grams)
        + 0.20 * salient_overlap(first.token_set, second.token_set)
    )
    if first.normalized_text == second.normalized_text:
        score += EXACT_NORMALIZED_TEXT_BOOST
    return min(score, 1.0)


def normalize_request_text(text: str) -> str:
    lowered = text.lower().replace("-", " ")
    return " ".join(re.findall(r"[a-z0-9_./]+", lowered))


def canonical_token(token: str) -> str:
    token = token.strip("./")
    if token in SYNONYMS:
        return SYNONYMS[token]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def char_grams(text: str, size: int = 4) -> frozenset[str]:
    if len(text) < size:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[index : index + size] for index in range(len(text) - size + 1))


def jaccard(first: frozenset[object], second: frozenset[object]) -> float:
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def salient_overlap(first: frozenset[str], second: frozenset[str]) -> float:
    first_salient = {token for token in first if token_is_salient(token)}
    second_salient = {token for token in second if token_is_salient(token)}
    return jaccard(frozenset(first_salient), frozenset(second_salient))


def token_is_salient(token: str) -> bool:
    return (
        "_" in token
        or "/" in token
        or "." in token
        or any(character.isdigit() for character in token)
    )

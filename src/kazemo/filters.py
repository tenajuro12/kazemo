"""Quality filters for the corpus. Each filter returns a reason string or ``None``.

Filters are deliberately simple and transparent so that every removed post can be
explained in a paper; removed posts are kept in a separate file with their reason.
"""

from __future__ import annotations

import re

from .preprocess import HASHTAG_RE, MENTION_RE, URL_RE, count_hashtags, remove_emojis

# Promotional vocabulary (Kazakh, Russian, English). Matched as whole-word prefixes.
AD_WORDS = [
    # kk
    "жеңілдік",
    "акция",
    "тапсырыс",
    "бағасы",
    "сатылымда",
    "жеткізу",
    "директке",
    "промокод",
    "ұтыс",
    # ru
    "скидк",
    "распродаж",
    "доставк",
    "заказ",
    "промокод",
    "розыгрыш",
    "цена",
    "оптом",
    "в наличии",
    "пишите в директ",
    "ссылка в био",
    "подписывайтесь",
    "реклам",
    "сотрудничеств",
    # en
    "ugc",
    "sale",
    "discount",
    "promo",
    "giveaway",
    "dm for",
    "link in bio",
    "order now",
    "collab",
    "sponsored",
]
AD_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in AD_WORDS) + r")", re.IGNORECASE)
PRICE_RE = re.compile(r"\d[\d\s.,]*\s?(?:тг|тенге|₸|теңге|kzt|руб|₽|\$|usd)(?!\w)", re.IGNORECASE)
CONTACT_RE = re.compile(r"(?:whatsapp|ватсап|wa\.me|<PHONE>)", re.IGNORECASE)

# Short courtesy formulas that contain emotion words but carry no real emotion.
FORMULAS = [
    "рахмет",
    "рақмет",
    "көп рахмет",
    "сау болыңыз",
    "сау бол",
    "пайдалы болғанына қуаныштымын",
    "танысқаныма қуаныштымын",
    "құттықтаймын",
    "иә",
    "жоқ",
    "спасибо",
    "благодарю",
    "пожалуйста",
    "согласен",
    "согласна",
    "да",
    "нет",
    "ок",
    "ok",
    "thanks",
    "thank you",
    "+",
    "++",
    "+1",
]
_FORMULA_SET = {f.lower() for f in FORMULAS}
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def words(text: str) -> list[str]:
    """Letter-only tokens, ignoring <URL>, @user_..., hashtags and emoji."""
    text = URL_RE.sub(" ", text.replace("<URL>", " ").replace("<PHONE>", " ").replace("<EMAIL>", " "))
    text = MENTION_RE.sub(" ", HASHTAG_RE.sub(" ", text))
    return WORD_RE.findall(remove_emojis(text))


def ad_score(text: str) -> int:
    """Number of promotional signals in a post."""
    score = min(len(AD_RE.findall(text)), 3)  # repeated promo words count, up to 3
    score += 1 if PRICE_RE.search(text) else 0
    score += 1 if CONTACT_RE.search(text) else 0
    score += 2 if count_hashtags(text) >= 5 else 0
    return score


def is_ad(text: str, threshold: int = 2) -> str | None:
    s = ad_score(text)
    return f"ad (score {s})" if s >= threshold else None


def is_formulaic(text: str, max_words: int = 5) -> str | None:
    ws = [w.lower() for w in words(text)]
    if not ws or len(ws) > max_words:
        return None
    phrase = " ".join(ws)
    if phrase in _FORMULA_SET or any(phrase.startswith(f + " ") or phrase == f for f in _FORMULA_SET if " " in f):
        return "formulaic"
    if all(w in _FORMULA_SET for w in ws):
        return "formulaic"
    return None


def is_too_short(text: str, min_words: int = 3) -> str | None:
    n = len(words(text))
    return f"too short ({n} words)" if n < min_words else None


def is_mostly_hashtags(text: str) -> str | None:
    tags = count_hashtags(text)
    n = len(words(text))
    return "mostly hashtags" if tags >= 3 and tags >= n else None

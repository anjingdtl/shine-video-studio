"""The script-specific terminology table that is handed to the ASR model.

A Chinese speech recognizer picks the wrong characters for a proper name far
more often than for ordinary words: "周砚" becomes "周烨" and "林知远" becomes
"林之远" are both plausible readings of the same audio.  Correcting that
afterwards means either trusting a fuzzy match or editing the transcript by
hand, and a fuzzy match would also hide a real mistake in the video.

The recognizer can do much better when it is told which words the script
actually uses.  This module keeps that table as project data that a human
maintains, and turns it into the context hint that the local ASR executor
accepts, so the decision happens *before* the transcript exists.

The table is a hint, never a proof.  It is never used to rewrite a
transcript: the transcript that the recognizer produced is the transcript
that the dialogue gate judges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TERMS = 200
MAX_TERM_LENGTH = 64


class TerminologyError(ValueError):
    """The terminology table is not usable as a hint."""


@dataclass(frozen=True)
class Term:
    """One term of the script and the spellings the recognizer may produce.

    The aliases are kept for the human — they say which mishearings are known
    for this name — but they are never sent to the recognizer, because a
    mishearing that is named in the context becomes an acceptable answer.
    """

    canonical: str
    aliases: tuple[str, ...] = ()

    def hint(self) -> str:
        return self.canonical


@dataclass(frozen=True)
class TerminologyTable:
    """The ordered terminology of one project."""

    terms: tuple[Term, ...] = ()
    notes: str = ""

    def canonical_terms(self) -> tuple[str, ...]:
        return tuple(term.canonical for term in self.terms)

    def hint(self) -> str:
        """The context string handed to the recognizer.

        It names every canonical term and nothing else.  The measured
        behaviour of the local recognizer is decisive here: when the known
        mishearings are listed next to the canonical spelling, the model
        treats every spelling in the context as acceptable and produces the
        wrong characters again.  Listing only the canonical spellings is what
        actually corrects a proper name.
        """
        if not self.terms:
            return ""
        body = "、".join(term.canonical for term in self.terms)
        return f"专有名词标准写法：{body}。"

    def sha256(self) -> str:
        import hashlib

        payload = "\n".join(
            [term.canonical + "|" + "|".join(term.aliases) for term in self.terms]
        ) + ("\n#" + self.notes if self.notes else "")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_table(rows: list[dict], notes: str = "") -> TerminologyTable:
    """Build a table from stored rows.

    The rows come from the database, so they are validated here instead of
    being trusted: a malformed row would otherwise become a hint that the
    recognizer follows in a wrong way.
    """
    terms: list[Term] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TerminologyError("TERMINOLOGY_ROW_INVALID")
        canonical = str(row.get("canonical") or "").strip()
        if not canonical:
            raise TerminologyError("TERMINOLOGY_TERM_EMPTY")
        if len(canonical) > MAX_TERM_LENGTH:
            raise TerminologyError("TERMINOLOGY_TERM_TOO_LONG")
        raw_aliases = row.get("aliases") or []
        if not isinstance(raw_aliases, list | tuple):
            raise TerminologyError("TERMINOLOGY_ALIASES_INVALID")
        aliases: list[str] = []
        for alias in raw_aliases:
            alias = str(alias or "").strip()
            if not alias:
                continue
            if len(alias) > MAX_TERM_LENGTH:
                raise TerminologyError("TERMINOLOGY_ALIAS_TOO_LONG")
            if alias not in aliases:
                aliases.append(alias)
        key = canonical.lower()
        if key in seen:
            raise TerminologyError("TERMINOLOGY_TERM_DUPLICATED")
        seen.add(key)
        terms.append(Term(canonical=canonical, aliases=tuple(aliases)))
        if len(terms) > MAX_TERMS:
            raise TerminologyError("TERMINOLOGY_TOO_MANY_TERMS")
    return TerminologyTable(terms=tuple(terms), notes=str(notes or "").strip())


_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章"
    "云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于"
    "时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈"
    "项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管"
    "卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家"
    "封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭历戎"
    "祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴鬱胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连"
    "茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍"
    "聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)


def terms_from_dialogue(dialogue: list) -> tuple[str, ...]:
    """The proper nouns that a script mentions, used to seed the table.

    The seed is a proposal for a human to confirm, never an automatic
    decision: a character name that the recognizer already reads correctly
    still belongs in the table, and a name that is not a name at all does not.
    A run of Chinese characters is split at a surname boundary, because a
    name is a surname plus a given name and not the whole phrase.
    """
    found: list[str] = []
    for line in dialogue:
        text = getattr(line, "text", None)
        if not isinstance(text, str) or not text:
            continue
        for run in re.findall(r"[一-龥]{2,}", text):
            for candidate in _split_at_surname(run):
                if candidate not in found:
                    found.append(candidate)
    return tuple(found[:MAX_TERMS])


def _split_at_surname(run: str) -> list[str]:
    """Split a run of Chinese characters at every surname that starts a name.

    "把陆清明的笔记本交给周砚" becomes "把", "陆清明", "的笔记本交给", "周砚".
    A Chinese name is a surname plus one or two given-name characters, so a
    surname that is followed by two more characters is treated as the start of
    a three character name: "陆清明" is one name, not "陆清" plus "明".
    """
    pieces: list[str] = []
    start = 0
    index = 1
    while index < len(run):
        if run[index] in _SURNAMES:
            # A three character name keeps its given name together.
            take = 3 if index + 2 < len(run) and run[index + 1] not in _SURNAMES else 2
            pieces.append(run[start:index])
            pieces.append(run[index:index + take])
            start = index + take
            index = start
            continue
        index += 1
    pieces.append(run[start:])
    return [piece for piece in pieces if piece]


__all__ = [
    "MAX_TERMS",
    "Term",
    "TerminologyError",
    "TerminologyTable",
    "parse_table",
    "terms_from_dialogue",
]

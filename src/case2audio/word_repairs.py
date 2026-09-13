"""Small, auditable spelling repairs; never general-purpose rewriting."""

from __future__ import annotations

import re

# These exact splits are PDF positioning artifacts; arbitrary word joining can change meaning.
SPLIT_WORDS = {
    "expa nd": "expand",
    "wou ld": "would",
    "acq uired": "acquired",
    "comman d": "command",
    "earn ed": "earned",
    "consumer s": "consumers",
    "i n": "in",
    "an d": "and",
    "tha t": "that",
    "Apr il": "April",
    "P oor": "Poor",
    "customer s": "customers",
    "GPU s": "GPUs",
    "price mins": "price minus",
    "out competes": "outcompetes",
    "merch andise": "merchandise",
    "w e": "we",
    "t o": "to",
    "o f": "of",
}

_COMPOUNDS = re.compile(
    r"\b(inflation)(adjusted)\b|\b(on|off)(premise)\b|\b(one)(third)\b|"
    r"\b(value)(based)\b|\b(front-of)(house)\b",
    re.I,
)

_KNOWN_FORMS = {
    "socalled": "so-called",
    "cofinanced": "co-financed",
    "latestgeneration": "latest-generation",
    "revenuebased": "revenue-based",
    "pandemicdistorted": "pandemic-distorted",
    "full - bodied": "full-bodied",
    "small - batch": "small-batch",
    "mid -year": "mid-year",
    "no - name": "no-name",
    "human - machine": "human-machine",
    # Spell out a famous logo because speech engines do not consistently pronounce the symbol.
    "I ♥ NY": "I Love New York",
    # This duplicated fragment is printed in the source and is uniquely unambiguous in speech.
    "Nvidia VIDIA": "Nvidia",
    # These are unambiguous source typos that would be especially distracting in speech.
    "Textiles used in collection": "Textiles used in the collection",
    "was adopted a wave of retailers": "was adopted by a wave of retailers",
    "such as such as": "such as",
    "produced about same total volume": "produced about the same total volume",
    "number one issue concern": "number one concern",
    "was often being their number one concern": "was often their number one concern",
    "almost a nearly identical piece": "a nearly identical piece",
    "orders of magnitudes cheaper": "orders of magnitude cheaper",
    "resuse": "reuse",
}


def repair_words(text: str) -> str:
    for damaged, fixed in SPLIT_WORDS.items():
        # Preserve normal case without lowercasing the rest of a proper noun or heading.
        def replace(match, fixed=fixed):
            original = match.group()
            if original.isupper():
                return fixed.upper()
            return fixed[0].upper() + fixed[1:] if original[0].isupper() else fixed.lower()

        text = re.sub(r"\b" + re.escape(damaged).replace(r"\ ", r"[ \t]+") + r"\b", replace, text)

    def compound(match):
        return "-".join(group for group in match.groups() if group is not None)

    text = _COMPOUNDS.sub(compound, text)
    for damaged, fixed in _KNOWN_FORMS.items():
        text = re.sub(re.escape(damaged), fixed, text, flags=re.I)
    # Mixed PDF quote objects can turn “case of ‘greenwashing’” into case of' greenwashing".
    text = re.sub(
        r"\bcase of\s*['\"]\s*([^'\"\n]+?)\s*['\"](?=:)",
        r"case of '\1'",
        text,
        flags=re.I,
    )
    return text

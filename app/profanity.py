"""Profanity filter for reviews (Russian, Kazakh, English).

This is basic protection with no external services. Words are matched by their beginning (allowing for prefixes), so
"скипидар" or "бледный" pass. Letter substitutions (Latin look-alikes of Cyrillic, digits, repeats) are accounted for.
The lists can be extended in RU / KK / EN below. Evasion cannot be ruled out entirely: moderation helps beyond that.
"""
import re

_LOOK = str.maketrans({"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к", "m": "м",
                       "t": "т", "h": "н", "b": "в", "0": "о", "3": "з", "@": "а", "ё": "е", "i": "и", "u": "у"})
_PRE = r"(?:на|по|за|вы|у|до|от|об|пере|при|раз|рас|с|ни|не|из|вз|во|под|съ|объ|разъ|ис)?"

RU = [re.compile(p) for p in (
    rf"^{_PRE}ху[йеяию]",                       # хуй, хуе..., хуя, нахуй, похуй
    rf"^{_PRE}пизд",                            # пизда, пиздец
    rf"^{_PRE}(?:еб(?:а|у|и|л|н|т|ы)|еб$)",       # ебать, ебан..., ебло
    r"^бля(?:д|т|х)",                           # блядь, блять
    r"^сук(?:а|и|у|ин)$|^сук(?:ин|ино)|^сучар|^сучк",
    r"^муд(?:ак|ач|ил|о)",                      # мудак
    r"^пид(?:о|а)р|^пидр|^педик|^педераст",
    r"^залуп|^гандон|^шлюх|^шалав|^дроч|^говн|^жоп(?:а|у|е|ы)|^мразь|^ублюд|^выблюд|^чмо$",
)]
KK = [re.compile(p) for p in (r"^сікт", r"^сикт", r"^аміңа", r"^амына", r"^амыңа", r"^сіқ")]
EN = [re.compile(p) for p in (
    r"^f+u+c+k", r"^sh[i1]+t", r"^b[i1]tch", r"^cunt", r"^asshole", r"^nigg", r"^whore", r"^slut",
    r"^dick(?!ens)", r"^pussy", r"^bastard", r"^motherf",
)]
# strong roots are also caught in "glued" text without spaces ("х у й", "п-и-з-д-а")
_STRONG = ("хуй", "пизд", "бляд", "залуп", "гандон", "fuck")

# Russian profanity written in Latin letters ("blyadskoe", "yebanov", "huy"): transliterate to Cyrillic and check the same way
_TL_MULTI = (("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ch", "ч"), ("sh", "ш"), ("ts", "ц"),
             ("yu", "ю"), ("ya", "я"), ("yo", "е"), ("ye", "е"), ("ju", "ю"), ("ja", "я"), ("jo", "е"), ("iu", "ю"), ("ia", "я"))
_TL_ONE = dict(a="а", b="б", c="к", d="д", e="е", f="ф", g="г", h="х", i="и", j="й", k="к", l="л", m="м", n="н",
               o="о", p="п", q="к", r="р", s="с", t="т", u="у", v="в", w="в", x="х", z="з")


def _translit(w: str, y: str) -> str:
    for a, b in _TL_MULTI:
        w = w.replace(a, b)
    return "".join(_TL_ONE.get(ch, y if ch == "y" else ch) for ch in w)


_WORD = re.compile(r"[a-zа-яёәіңғүұқөһ0-9@]+", re.I)


def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


def has_profanity(text: str) -> bool:
    low = (text or "").lower()
    for w in _WORD.findall(low):
        forms = {w, _collapse(w), w.translate(_LOOK), _collapse(w.translate(_LOOK))}
        if re.fullmatch(r"[a-z]+", w):
            for y in ("й", "ы"):
                forms.add(_translit(w, y)); forms.add(_collapse(_translit(w, y)))
        for f in forms:
            if any(p.search(f) for p in RU + KK + EN):
                return True
    glued = _collapse(re.sub(r"[^a-zа-яёәіңғүұқөһ0-9@]+", "", low))
    glued_cyr = glued.translate(_LOOK)
    glued_tl = _collapse(_translit(glued, "й")) if re.fullmatch(r"[a-z]+", glued) else ""
    return any(s in glued or s in glued_cyr or s in glued_tl for s in _STRONG)

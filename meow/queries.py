"""Turning a spoken question into searches that find something.

A person asking out loud does not produce a good query. "hey can you do a
research on solar panel cost and put it in the spreadsheet and the research
should be based on India" is a sentence, and handing it to a search engine
whole gets pages that match its shape rather than its subject. It also carries
the output format - "put it in a spreadsheet" - which has nothing to do with
what is being looked for and everything to do with what happens afterwards.

So the question is rewritten into a few short queries first.

**Several, not one, and deliberately from different angles.** One query returns
one slice of one ranking. "solar panel cost india" and "solar panel price per
watt india 2026" reach different pages, and the overlap between them is a
signal in itself: a figure that appears in both is worth more than one that
appears in the top result.

**Model-free when it can be.** The stripping - dropping the pleasantries and
the output format - is rules, because they are the same every time and a model
call to remove "hey can you" is a model call spent on nothing. The angles need
a model, because knowing that solar panels have a price per watt is knowledge
rather than grammar. When the model is unavailable the stripped question is
used alone, which is worse and still works.

**This runs BEFORE anything is fetched.** It sees only the user's own words, so
no untrusted content reaches the model that writes the queries. That ordering
is not incidental - a page that could influence the next search could walk the
research anywhere it liked.
"""

from __future__ import annotations

import re

# Said to a person, meaningless to a search engine.
PLEASANTRIES = (
    "hey", "hi", "hello", "ok", "okay", "so", "um", "uh", "please", "can you",
    "could you", "would you", "will you", "i want you to", "i need you to",
    "i would like you to", "for me", "thanks", "thank you", "meow",
)

# What to DO with the answer, which is not part of finding it. Patterns rather
# than literal strings: people say "put it in A spreadsheet" and "put it in THE
# spreadsheet" and "make a spreadsheet out of it", and a list of exact phrases
# catches whichever one happened to be written down. The first version missed
# "the" and left "solar panel cost and put it in the spreadsheet and the
# research should be based on india" as the query.
ARTEFACT = r"(?:spreadsheet|document|doc|deck|slides|presentation|file|note|report)"

OUTPUT_PATTERNS = (
    # ...and put it in a spreadsheet / save them into the document
    rf"\b(?:and\s+|then\s+)?(?:put|save|write|stick|add)\s+(?:it|them|that|"
    rf"this)?\s*(?:in|into|to|on)\s+(?:a|the|my)?\s*{ARTEFACT}\b",
    # ...make a spreadsheet out of it
    rf"\b(?:and\s+|then\s+)?(?:make|create|build|generate)\s+(?:me\s+)?"
    rf"(?:a|the|an)?\s*{ARTEFACT}(?:\s+out\s+of\s+(?:it|them|this|that))?\b",
    # ...as a spreadsheet / in a document
    rf"\b(?:as|in)\s+(?:a|the|an)?\s*{ARTEFACT}\b",
    # the asking verbs themselves
    r"\b(?:do|find|get|run)\s+(?:a|some|the)?\s*research\s+(?:on|about|"
    r"into|for)\b",
    r"\b(?:research|look\s+up|search|find\s+out|read\s+up)\s+(?:on|about|"
    r"into|for)\b",
    r"\b(?:tell|show)\s+me\s+(?:about|what)\b",
    r"\b(?:and|then)\s+(?:open|save|show)\s+(?:it|them|that)\b",
    r"\bwrite\s+it\s+up\b",
    r"\bfor\s+me\b",
    # "the research should be based on india" -> "india"
    r"\b(?:the\s+)?research\s+should\s+be\s+based\s+(?:on|in)\b",
    r"\b(?:all\s+the|all)\b",
    r"\b(?:like)\b",
    r"\bout\s+of\s+(?:it|them|this|that)\b",
    # A bare asking verb at the START, with no "on" or "about" after
    # it: "research gpu prices", "look up how to change my dns".
    r"^(?:can\s+you\s+)?(?:do\s+)?(?:a\s+)?(?:research|look\s+up|"
    r"search|find|get|fetch)\s+",
)


# A query longer than this is a sentence, and search engines do worse with
# sentences than with the four words that matter in them.
MAX_QUERY_WORDS = 10
MAX_QUERIES = 3


def strip_question(spoken: str) -> str:
    """The subject of the question, without the politeness or the plumbing.

    Rules rather than a model: these phrases are the same every time, and
    spending a model call to remove "hey can you" is a model call spent on
    nothing.
    """
    text = " ".join(str(spoken).split()).lower().strip(" .?!,")
    if not text:
        return ""

    for pattern in OUTPUT_PATTERNS:
        text = re.sub(pattern, " ", text)
    for phrase in sorted(PLEASANTRIES, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)

    # Conjunctions left stranded by the removals. "cost and put it in the
    # spreadsheet and based on india" becomes "cost   and   india" unless the
    # orphans go too.
    text = re.sub(r"\s+(?:and|then|also)\s+(?:and|then|also)\s+", " ", text)
    text = re.sub(r"^\s*(?:and|then|also|about|on|of)\s+", " ", text)
    text = re.sub(r"\s+(?:and|then|also)\s*$", " ", text)

    # Stopwords are NOT removed. Search engines handle them, and dropping
    # them turns "cost of solar in india" into "cost solar india" - the same
    # query with worse phrase matching.
    text = " ".join(text.split())
    text = text.strip(" .?!,-")
    return text


def shorten(text: str, limit: int = MAX_QUERY_WORDS) -> str:
    words = text.split()
    return " ".join(words[:limit]) if len(words) > limit else text


def plain_queries(spoken: str) -> list[str]:
    """What to search for, without asking a model. Always returns something.

    The fallback, and the floor: however the model round goes, the stripped
    question is a usable search on its own.
    """
    stripped = strip_question(spoken)
    if not stripped:
        return []
    return [shorten(stripped)]


def rewrite(spoken: str, model=None, limit: int = MAX_QUERIES) -> list[str]:
    """A few short queries, from different angles.

    `model` is anything with `.invoke([messages]) -> message`. Absent, or
    failing, the stripped question is returned alone - research that works
    worse is better than research that does not run.
    """
    stripped = strip_question(spoken)
    if not stripped:
        return []

    queries = [shorten(stripped)]
    if model is None:
        return queries

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        reply = model.invoke([
            SystemMessage(
                "Write web search queries for the question. Rules:\n"
                "- Between 2 and 3 queries, one per line, nothing else.\n"
                "- Keywords, not sentences. Under 10 words each.\n"
                "- Each from a DIFFERENT angle: a different wording, a "
                "narrower aspect, or the specific figure or name someone "
                "would look for.\n"
                "- Keep any place, year or product that was specified.\n"
                "- Never include what to DO with the answer."),
            HumanMessage(stripped),
        ])
        for line in str(reply.content).splitlines():
            candidate = line.strip(" -*0123456789.\t")
            candidate = " ".join(candidate.split())
            if not candidate or len(candidate) < 3:
                continue
            candidate = shorten(candidate)
            if candidate.lower() not in {q.lower() for q in queries}:
                queries.append(candidate)
            if len(queries) >= limit:
                break
    except Exception:  # noqa: BLE001 - the floor still stands
        pass

    return queries[:limit]

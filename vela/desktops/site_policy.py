"""What an agent may *cause* on a website, as opposed to what it may look at.

Approving a site is two decisions, and the interface that treats them as one is
the interface that surprises somebody. `vela/desktops/policy.py` holds the first
— which origins this desktop's browser may reach at all — and this holds the
second: whether the agent may do something on one of them, and what has to
happen before it does.

Three honest answers, and no fourth:

**Read.** A GET or a HEAD. The page is fetched and the agent looks at it.

**A submission.** A request that would change something, in a shape Vela can
describe: a form post from a page, or an API call the page made, with a method,
an origin, a path and the *names* of the fields it carries. That description is
what an owner approves, and the approval is bound to its digest, so saying yes
to one submission is not saying yes to the next one.

**Something Vela cannot classify.** A beacon, a service worker's fetch, a body
in a shape nothing here understands. There is no honest prompt to write for
these, so they are not offered as a prompt. The task pauses and asks for a
person, who can take control and do it themselves.

Nothing here decides that *arbitrary sites are read-only because only GET is
allowed*. A GET changes things on plenty of sites. What this file claims is
narrower and true: Vela refuses to cause what it cannot describe, describes what
it can, and binds the owner's answer to the exact request it was given for.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlparse

#: What an owner may say about a site. `read` is the default and the safe one:
#: the agent may browse, and anything that would change something needs a person
#: at the keyboard. `ask` opens the described-and-bound approval path instead.
SITE_EFFECT_MODES = ("read", "ask")

#: Methods that are a request for something rather than a change to it. Not a
#: claim that a GET can never have an effect — a claim that these are the ones
#: an agent may issue without being described first.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Request kinds a submission may be. Anything else — a beacon, a ping, a
#: service worker's own fetch, a media range request — is unclassified, because
#: the page, not the agent, decided to make it and nobody can say what it means.
DESCRIBABLE_TYPES = frozenset({"document", "xhr", "fetch"})

#: Body shapes whose *field names* can be read out without reading the values.
#: A body in any other shape is described as bytes, and bytes are not something
#: to put in front of a person as if they understood it.
DESCRIBABLE_BODIES = frozenset(
    {
        "",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "application/json",
        "text/plain",
    }
)

#: Most field names repeated back in a prompt.
MAX_FIELDS = 12

#: Longest request body a submission may carry before it stops being something
#: described in a sentence. Larger than this is an upload, and an upload goes
#: through the artifact path where the file itself was chosen deliberately.
MAX_DESCRIBED_BODY = 2 * 1024 * 1024


class SiteEffect:
    """One classified request, and what the desktop's rules say about it."""

    __slots__ = (
        "kind",
        "decision",
        "reason",
        "method",
        "origin",
        "path",
        "fields",
        "bytes",
        "digest",
        "readable",
    )

    def __init__(self, **fields: Any):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


def effects_mode(rule: Any) -> str:
    """A site rule's effect mode, defaulting the way an unwritten rule should.

    Rules stored before sites had an effect mode have none, and the answer for
    those is `read` — the narrower of the two. A missing setting must never read
    as the more permissive one.
    """
    if not isinstance(rule, dict):
        return "read"
    mode = rule.get("effects")
    return mode if mode in SITE_EFFECT_MODES else "read"


def normalize(url: str) -> tuple[str, str]:
    """A URL as `(origin, path)`, both lower-cased where case is not meaningful.

    The query is deliberately not in the path: it goes into the digest, where it
    binds the approval, rather than into the sentence a person reads.
    """
    parsed = urlparse(str(url or ""))
    origin = f"{parsed.scheme}://{parsed.netloc}".lower()
    return origin, parsed.path or "/"


def classify(request: Any) -> SiteEffect:
    """What one request the browser is about to make actually is.

    `request` is the worker's description: method, url, resource type, content
    type, the field *names* it found and the size and digest of the body. Values
    never travel: a password in a prompt is a password in a screenshot.
    """
    request = request if isinstance(request, dict) else {}
    method = str(request.get("method") or "GET").upper()
    origin, path = normalize(request.get("url"))
    fields = [str(name)[:80] for name in (request.get("fields") or [])][:MAX_FIELDS]
    size = int(request.get("bodyBytes") or 0)
    content_type = str(request.get("contentType") or "").split(";")[0].strip().lower()
    resource = str(request.get("resourceType") or "").lower()

    readable = bool(request.get("bodyAvailable", True))
    common = {
        "method": method,
        "origin": origin,
        "path": path,
        "fields": fields,
        "bytes": size,
        "readable": readable,
        "digest": digest(request),
    }
    if method in SAFE_METHODS:
        return SiteEffect(kind="read", reason="a request for something, not a change", **common)
    if resource not in DESCRIBABLE_TYPES:
        return SiteEffect(
            kind="unsupported",
            reason=f"Vela cannot describe a {resource or 'background'} request well enough to ask about it",
            **common,
        )
    if content_type not in DESCRIBABLE_BODIES:
        return SiteEffect(
            kind="unsupported",
            reason=f"Vela cannot describe a {content_type} body well enough to ask about it",
            **common,
        )
    if size > MAX_DESCRIBED_BODY:
        return SiteEffect(
            kind="unsupported",
            reason="that request carries more than Vela can describe in a question",
            **common,
        )
    if not readable:
        # A body the browser holds as a stream. That is what a file upload is,
        # and it is the one case worth describing anyway: the address, the
        # method and "it is sending a file" is a true sentence, and the file itself
        # was chosen by the owner or downloaded under this desktop's own rules.
        # Anything else unreadable has no such story and is not guessed at.
        if content_type != "multipart/form-data":
            return SiteEffect(
                kind="unsupported",
                reason="Vela cannot read what that request is carrying",
                **common,
            )
        return SiteEffect(
            kind="submission",
            reason="a request that would send a file",
            **common,
        )
    return SiteEffect(kind="submission", reason="a request that would change something", **common)


def decide(rule: Any, effect: SiteEffect) -> str:
    """`allow`, `ask` or `person`, from the site's own rule.

    `person` is not a refusal to help. It is the honest answer when there is no
    sentence Vela could put in front of somebody: take over the window and do it
    yourself, with your own hands, in the same browser.
    """
    if effect.kind == "read":
        return "allow"
    if effect.kind == "unsupported":
        return "person"
    return "ask" if effects_mode(rule) == "ask" else "person"


def digest(request: Any) -> str:
    """The binding an approval is bound to.

    Method, the full URL including its query, the content type, the field names
    and the digest the worker took of the body. Change any of them and this is a
    different request needing a different answer — which is the whole point.
    """
    request = request if isinstance(request, dict) else {}
    material = {
        "method": str(request.get("method") or "GET").upper(),
        "url": str(request.get("url") or ""),
        "contentType": str(request.get("contentType") or ""),
        "fields": sorted(str(name) for name in (request.get("fields") or [])),
        "body": str(request.get("bodyDigest") or ""),
        # Whether the body could be read at all is part of what was approved. An
        # answer given about a request Vela could describe must not also cover
        # one it could not.
        "readable": bool(request.get("bodyAvailable", True)),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def contract(rule: Any, policy_revision: int) -> str:
    """What the owner's answer was reviewed against.

    The site rule and the revision of the policy it came from. Change either and
    every grant issued under the old one stops matching, the same way an app's
    grants stop matching when the app is reinstalled.
    """
    material = {
        "origin": (rule or {}).get("origin"),
        "includeSubdomains": bool((rule or {}).get("includeSubdomains")),
        "effects": effects_mode(rule),
        "policyRevision": int(policy_revision or 0),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return "site:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def principal(origin: str) -> str:
    """The name a site takes in the grant table, beside the app ids.

    Prefixed so it can never collide with an installed app's id, which is
    lower-case letters, digits and hyphens and contains no colon.
    """
    return f"site:{origin}"


def summarize(effect: SiteEffect) -> dict[str, Any]:
    """The prompt's own words, built from the request and nothing else."""
    host = effect.origin.split("//", 1)[-1] if effect.origin else "a website"
    detail = [f"{effect.method} {effect.path}"]
    if not effect.readable:
        # The honest version of an upload. Vela knows a file is going and cannot
        # know which one from the request, so it says that rather than listing a
        # field list it does not have.
        detail.append("It is sending a file. Vela cannot read what is in it from here.")
    elif effect.fields:
        detail.append("Fields: " + ", ".join(effect.fields))
    elif effect.bytes:
        detail.append(f"Carries {effect.bytes} bytes Vela is not reading.")
    else:
        detail.append("With nothing attached.")
    detail.append("Vela shows what is being sent, not the values in it.")
    return {
        "effect": "submit",
        "headline": f"The agent wants to send something to {host}.",
        "detail": detail,
        # Never complete: Vela describes the shape of an external request and
        # cannot know what the site will do with it.
        "complete": False,
    }

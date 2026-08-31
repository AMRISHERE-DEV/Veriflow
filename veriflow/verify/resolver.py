"""
Resolver: the lead -> resolved -> admitted boundary.

A model may emit only a Lead (a locator + advisory metadata, never content or a
hash). A ResolverFn independently resolves a Lead to a ResolvedSource whose
content and hash are minted from the RESOLVER's own bytes - never the model's.
record_from_resolved() bridges a ResolvedSource into an EvidenceRecord that the
existing admissibility/status spine consumes unchanged.

Two containment rules are made mechanical here:
  * "lead != evidence": an unresolved (or binding-failed) Lead becomes a record
    with trusted_hash=None / applicable=False, which admit()+decide_status already
    exclude from support and contradiction. Fail-open: never negative evidence.
  * "self-hash != source proof": for a Lead, trusted content/hash are minted only
    from the resolver's OWN bytes. (resolved_by / source_bound are an observability
    LABEL, not a forgery-proof attestation: a caller hand-building an EvidenceRecord
    could set them but gains no admission or status power - see
    test_forged_resolved_by_grants_no_status_power. Promote to a gate only once made
    unforgeable.)

A Lead's stance is advisory ONLY and never reaches the admitted item: a resolved
record is always created NEUTRAL, and its entailment is (re)computed solely by
admit()'s entailment_fn on the resolver's content. This closes both ENTAILS and
CONTRADICTS injection. Full quote-span binding for entailment is build #4.

The in-memory resolver is a deterministic, offline FIXTURE (stdlib only; performs
no network or wall-clock access) - not itself a source of external authority. Real
DOI/Crossref/PMID/url resolution plugs in behind the same ResolverFn seam (see the
http_resolver stub) and routes any model assistance through the gateway.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from html.parser import HTMLParser
from typing import Protocol, cast

from .admissibility import EvidenceRecord, evidence_source_subject
from .status import AuthorityTier, Entailment
from .trust import issue_trusted_source_proof

_RESOLVER_TRUST_TOKEN = object()


@dataclass(frozen=True)
class Lead:
    """The ONLY shape a model may emit. No content, no hash."""
    lineage_id: str
    locator: str
    claimed_published: datetime
    claimed_authority: AuthorityTier = AuthorityTier.WEB   # advisory; never copied to the item
    suggested_quote: str = ""                              # binding-checked; never used as content
    stance: Entailment = Entailment.NEUTRAL                # advisory ONLY; never reaches the item
    applicable: bool = True
    strength: float = 1.0

    @staticmethod
    def suggest(lineage_id: str, locator: str, *, claimed_published: datetime,
                claimed_authority: AuthorityTier = AuthorityTier.WEB,
                suggested_quote: str = "", stance: Entailment = Entailment.NEUTRAL,
                applicable: bool = True, strength: float = 1.0) -> Lead:
        return Lead(lineage_id=lineage_id, locator=locator, claimed_published=claimed_published,
                    claimed_authority=claimed_authority, suggested_quote=suggested_quote,
                    stance=stance, applicable=applicable, strength=strength)


@dataclass(frozen=True)
class ResolvedSource:
    """The only object that legitimately holds source content + hash together."""
    lineage_id: str
    resolved_identity: str
    content: str                 # the resolver's OWN bytes (model content discarded)
    source_hash: str             # sha256(content), minted INSIDE the resolver
    authority_tier: AuthorityTier
    published: datetime
    resolver_id: str
    binding_ok: bool
    quote: str = ""              # the lead's suggested_quote, carried so admit()'s span binding applies


@dataclass(frozen=True)
class InadmissibleLead:
    """Fail-open record of WHY a lead did not resolve (kept visible)."""
    lineage_id: str
    locator: str
    reason: str   # "unknown_locator" | "binding_mismatch" | "resolver_error"


ResolverFn = Callable[[Lead], ResolvedSource | None]


class _StampableResolver(Protocol):
    _veriflow_resolver_trust_token: object
    _veriflow_resolver_id: str

    def __call__(self, lead: Lead) -> ResolvedSource | None: ...


def issue_trusted_resolver(resolver_fn: ResolverFn, *, resolver_id: str) -> ResolverFn:
    """Operator-plane registration for an approved resolver implementation.

    The private identity cannot be supplied through JSON or ordinary evidence
    fields. Deployments must keep this issuance point away from tenant code, just
    like the other trust factories in :mod:`veriflow.verify.trust`.
    """
    stampable = cast(_StampableResolver, resolver_fn)
    stampable._veriflow_resolver_trust_token = _RESOLVER_TRUST_TOKEN
    stampable._veriflow_resolver_id = resolver_id
    return resolver_fn


def resolver_is_trusted(resolver_fn: ResolverFn | None, *, resolver_id: str = "") -> bool:
    return bool(
        resolver_fn is not None
        and getattr(resolver_fn, "_veriflow_resolver_trust_token", None) is _RESOLVER_TRUST_TOKEN
        and (not resolver_id or getattr(resolver_fn, "_veriflow_resolver_id", None) == resolver_id)
    )


def chain_resolvers(*resolvers: ResolverFn, resolver_id: str = "resolver-chain") -> ResolverFn:
    """Compose approved resolvers without losing the source-proof boundary."""
    if not resolvers or not all(resolver_is_trusted(item) for item in resolvers):
        raise ValueError("resolver chains require at least one trusted resolver")

    def _chain(lead: Lead) -> ResolvedSource | None:
        for resolver in resolvers:
            resolved = resolver(lead)
            if resolved is not None:
                return replace(resolved, resolver_id=resolver_id)
        return None

    return issue_trusted_resolver(_chain, resolver_id=resolver_id)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def record_from_resolved(rs: ResolvedSource, *, applicable: bool = True,
                         strength: float = 1.0,
                         issue_source_proof: bool = False) -> EvidenceRecord:
    """Bridge a ResolvedSource into an EvidenceRecord. The record is created with
    stance=NEUTRAL ALWAYS (a lead's stance never reaches it); admit()'s entailment_fn
    recomputes the stance from rs.content. binding_ok=False => applicable=False. The
    lead's suggested_quote is carried onto the record (Slice 1) so that a model-derived
    entailment over resolver bytes counts only when carried by a quote that binds."""
    record = EvidenceRecord(
        lineage_id=rs.lineage_id, content=rs.content, authority_tier=rs.authority_tier,
        published=rs.published, trusted_hash=rs.source_hash, stance=Entailment.NEUTRAL,
        applicable=(applicable and rs.binding_ok), strength=strength, resolved_by=rs.resolver_id,
        resolved_identity=rs.resolved_identity, quote=rs.quote,
    )
    if not issue_source_proof:
        return record
    proof = issue_trusted_source_proof(
        subject=evidence_source_subject(record),
        resolver_id=rs.resolver_id,
        detail="resolver-owned source bytes",
    )
    return replace(record, source_proof=proof)


def _unresolved_record(lead: Lead) -> EvidenceRecord:
    """Fail-open placeholder for an unresolvable lead: cannot support or contradict
    (trusted_hash=None => integrity False; applicable=False; NEUTRAL)."""
    return EvidenceRecord(
        lineage_id=lead.lineage_id, content="", authority_tier=AuthorityTier.UNKNOWN,
        published=lead.claimed_published, trusted_hash=None, stance=Entailment.NEUTRAL,
        applicable=False, strength=0.0, resolved_by=None,
    )


def resolve(items: Sequence, *, resolver_fn: ResolverFn | None):
    """Map a mixed sequence of (Lead | EvidenceRecord) to (records, inadmissible_leads).

    - EvidenceRecord (legacy)         -> passed through UNCHANGED.
    - Lead, no resolver_fn / miss     -> InadmissibleLead("unknown_locator") + placeholder record.
    - Lead, resolver raises           -> InadmissibleLead("resolver_error") + placeholder record.
    - Lead, resolved, binding_ok      -> record_from_resolved(rs).
    - Lead, resolved, not binding_ok  -> InadmissibleLead("binding_mismatch") + record_from_resolved(rs)
                                         (applicable=False, NEUTRAL -> cannot support/contradict)."""
    records: list = []
    inadmissible: list = []
    for it in items:
        if not isinstance(it, Lead):
            records.append(it)          # legacy EvidenceRecord: untouched
            continue
        if resolver_fn is None:
            inadmissible.append(InadmissibleLead(it.lineage_id, it.locator, "unknown_locator"))
            records.append(_unresolved_record(it))
            continue
        try:
            rs = resolver_fn(it)
        except Exception:
            inadmissible.append(InadmissibleLead(it.lineage_id, it.locator, "resolver_error"))
            records.append(_unresolved_record(it))
            continue
        if rs is None:
            inadmissible.append(InadmissibleLead(it.lineage_id, it.locator, "unknown_locator"))
            records.append(_unresolved_record(it))
            continue
        if not rs.binding_ok:
            inadmissible.append(InadmissibleLead(it.lineage_id, it.locator, "binding_mismatch"))
        records.append(record_from_resolved(
            rs,
            applicable=it.applicable,
            strength=it.strength,
            issue_source_proof=resolver_is_trusted(resolver_fn, resolver_id=rs.resolver_id),
        ))
    return records, inadmissible


def make_inmemory_resolver(catalog: dict, *, resolver_id: str = "inmemory") -> ResolverFn:
    """Deterministic, offline resolver over a fixture catalog.

    catalog maps locator -> dict(content, resolved_identity, authority_tier, published).
    Unknown locator -> None. source_hash is minted from the CATALOG's bytes; binding_ok
    is True iff suggested_quote is empty or a normalized substring of the catalog content.
    Performs no network or wall-clock access - a deterministic test fixture, not an authority."""
    def _resolver(lead: Lead) -> ResolvedSource | None:
        entry = catalog.get(lead.locator)
        if entry is None:
            return None
        content = entry["content"]
        quote = lead.suggested_quote
        binding_ok = (quote == "") or (_norm(quote) in _norm(content))
        return ResolvedSource(
            lineage_id=lead.lineage_id,
            resolved_identity=entry.get("resolved_identity", lead.locator),
            content=content,
            source_hash=hashlib.sha256(content.encode()).hexdigest(),
            authority_tier=entry.get("authority_tier", AuthorityTier.WEB),
            published=entry["published"],
            resolver_id=resolver_id,
            binding_ok=binding_ok,
            quote=quote,
        )
    return issue_trusted_resolver(_resolver, resolver_id=resolver_id)


# --- Reality Contact v0: real resolution behind the SAME ResolverFn seam ----- #
@dataclass(frozen=True)
class FetchResult:
    """What a (real) fetcher returns. EVERY value is resolver-owned, minted from the
    FETCH, never from the model's locator/claims."""
    content: str
    final_url: str
    resolved_identity: str           # resolver-confirmed: canonical id or the final fetched URL
    published: datetime | None    # from a reliable source signal (Last-Modified); else None
    source_type: str                 # "url" | "doi" | "arxiv" | "pmid"
    authority_tier: AuthorityTier    # resolver-assigned by SOURCE TYPE (never model-assigned)


def make_http_resolver(*, fetch, default_published: datetime, resolver_id: str = "http") -> ResolverFn:
    """Real resolver behind ResolverFn. Resolves a lead's locator via an INJECTED `fetch`
    (so it is testable offline). Mints content / hash / resolved_identity / authority_tier /
    published from the FETCH RESULT - NEVER from the model's locator or claims.

      * fetch -> None (failure, non-200, empty) => return None => resolve() fails OPEN to
        InadmissibleLead + UNVERIFIED. The model can never turn an unresolved lead into support.
      * resolved_identity is resolver-confirmed (canonical id / final URL), never lead.locator,
        so the model's chosen locator is not an independence key.
      * Unknown publication date => `default_published`, a SYSTEM value (recommend a CONSERVATIVE
        OLD date), never lead.claimed_published: absence of a date must never manufacture recency.
    """
    def _resolver(lead: Lead) -> ResolvedSource | None:
        res = fetch(lead.locator)
        if res is None:
            return None
        content = res.content
        quote = lead.suggested_quote
        binding_ok = (quote == "") or (_norm(quote) in _norm(content))
        return ResolvedSource(
            lineage_id=lead.lineage_id,
            resolved_identity=res.resolved_identity,          # resolver-confirmed, NOT the locator
            content=content,
            source_hash=hashlib.sha256(content.encode()).hexdigest(),   # resolver-owned bytes
            authority_tier=res.authority_tier,                # by source type, not the model
            published=res.published or default_published,     # source date or SYSTEM default (never the lead's)
            resolver_id=resolver_id,
            binding_ok=binding_ok,
            quote=quote,
        )
    return issue_trusted_resolver(_resolver, resolver_id=resolver_id)


# Max bytes read from a fetched body before we give up (finding #7). A large/slow
# page must never reach the full-body regex in _crude_text and exhaust CPU/memory;
# we read at most this many bytes and, if the body is at least this big, fail OPEN
# (return None) rather than process an unbounded body.
_MAX_FETCH_BYTES = 5 * 1024 * 1024  # 5 MiB

_STRIP_TAGS = re.compile(r"(?s)<[^>]+>")


_PARA_RE = re.compile(r"(?is)<(p|h[1-6]|li|blockquote|td)\b[^>]*>(.*?)</\1>")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, _attrs):
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data:
            self._chunks.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._chunks)).strip()


def _crude_text(html: str) -> str:
    """Coarse HTML -> text (stdlib only; documented as crude). Prefers ARTICLE-BODY blocks
    (<p>/<h*>/<li>/...) over a raw strip, so navigation/menu chrome does not crowd out (and
    truncate away) the actual content. Falls back to a full strip when no body blocks are found."""
    parser = _TextExtractor()
    parser.feed(html)
    cleaned = parser.text()
    if not cleaned:
        cleaned = re.sub(r"\s+", " ", _STRIP_TAGS.sub(" ", html)).strip()
    blocks = []
    for match in _PARA_RE.finditer(html):
        block_parser = _TextExtractor()
        block_parser.feed(match.group(2))
        block = block_parser.text()
        if block:
            blocks.append(block)
    body = re.sub(r"\s+", " ", " ".join(blocks)).strip()
    if len(body) >= 200:                       # substantial article body recovered
        return body
    return cleaned


def _expand_locator(locator: str) -> str:
    """Map a bare doi:/arxiv:/pmid: locator to its canonical URL; pass URLs through."""
    low = locator.strip()
    if low.startswith(("http://", "https://")):
        return low
    if low.startswith("doi:"):
        return "https://doi.org/" + low[4:]
    if low.startswith("arxiv:"):
        return "https://arxiv.org/abs/" + low[6:]
    if low.startswith("pmid:"):
        return "https://pubmed.ncbi.nlm.nih.gov/" + low[5:]
    return low


def _canonical_identity(final_url: str) -> str:
    """Resolver-confirmed identity from the FINAL fetched URL: a canonical id when the URL
    pattern yields one, else the final URL itself (the real location, not the model's locator)."""
    u = final_url.lower()
    m = re.search(r"arxiv\.org/abs/([\w./-]+)", u)
    if m:
        return "arxiv:" + m.group(1)
    m = re.search(r"doi\.org/(10\.[^\s?#]+)", u)
    if m:
        return "doi:" + m.group(1)
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", u)
    if m:
        return "pmid:" + m.group(1)
    return final_url


def _identity_tier(identity: str):
    if identity.startswith(("doi:", "pmid:", "arxiv:")):
        return identity.split(":", 1)[0], AuthorityTier.PEER_REVIEWED
    return "url", AuthorityTier.WEB


def _read_capped(resp, *, max_bytes: int = _MAX_FETCH_BYTES) -> bytes | None:
    """Read at most max_bytes+1 from a response stream and fail OPEN (return None)
    if the body is at least max_bytes (i.e. it would exceed the cap). Reading one
    extra byte lets us distinguish "exactly at cap" from "over cap" without ever
    materializing an unbounded body (finding #7). Tests inject a fake response with
    a `.read(n)` method to prove oversized bodies are bounded without the network."""
    raw = resp.read(max_bytes + 1)
    if not isinstance(raw, (bytes, bytearray)):
        return None
    if len(raw) > max_bytes:
        return None                         # oversized body -> fail open, never processed
    return bytes(raw)


def _resolve_host(host: str) -> tuple[str, ...]:
    return tuple(sorted({str(item[4][0]) for item in socket.getaddrinfo(host, None)}))


def _is_public_http_url(url: str, host_resolver) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local")):
            return False
        addresses = host_resolver(host)
        if not addresses:
            return False
        for raw in addresses:
            address = ipaddress.ip_address(raw)
            if not address.is_global:
                return False
        return True
    except (ValueError, OSError, socket.gaierror):
        return False


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, host_resolver):
        self._host_resolver = host_resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_public_http_url(newurl, self._host_resolver):
            raise urllib.error.URLError("redirect target is not a public HTTP(S) URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_public_url(req, *, timeout: float, host_resolver):
    opener = urllib.request.build_opener(_PublicOnlyRedirectHandler(host_resolver))
    # Initial and redirected URLs are checked against public resolved addresses.
    return opener.open(req, timeout=timeout)


def urllib_fetch(url: str, *, timeout: float = 10.0, host_resolver=None,
                 opener=None) -> FetchResult | None:
    """The LIVE fetcher (stdlib urllib). NOT used in unit tests - tests inject a fake `fetch`.
    GET the (expanded) locator; mint a FetchResult from the FETCHED bytes; return None on any
    failure so the resolver fails open. The body is read with a BYTE CAP (finding #7): a
    large/slow page never reaches the full-body regex. Publication date uses Last-Modified
    ONLY (never the Date header, which is fetch time, not publication); else None -> the
    resolver's default_published applies."""
    from email.utils import parsedate_to_datetime
    target = _expand_locator(url)
    host_resolver = host_resolver or _resolve_host
    if not _is_public_http_url(target, host_resolver):
        return None
    try:
        req = urllib.request.Request(  # noqa: S310 - target is public HTTP(S), checked above.
            target, headers={"User-Agent": "VeriFlow/0.3 (+research)"})
        open_url = opener or _open_public_url
        with open_url(req, timeout=timeout, host_resolver=host_resolver) as resp:
            # Accept ANY 2xx success, not just 200: PubMed answers 203 (Non-Authoritative
            # Information), and rejecting it made the primary scientific source type
            # unusable as evidence. Anything outside 2xx still fails open.
            status = getattr(resp, "status", None)
            if status is not None and not (200 <= int(status) < 300):
                return None
            raw = _read_capped(resp)
            if raw is None:                 # oversized / unreadable body -> fail open
                return None
            final_url = resp.geturl()
            if not _is_public_http_url(final_url, host_resolver):
                return None
            last_mod = resp.headers.get("Last-Modified")
    except Exception:
        return None
    text = _crude_text(raw.decode("utf-8", errors="replace"))
    if not text.strip():
        return None
    published = None
    if last_mod:
        try:
            published = parsedate_to_datetime(last_mod)
        except Exception:
            published = None
    identity = _canonical_identity(final_url)
    source_type, tier = _identity_tier(identity)
    return FetchResult(content=text, final_url=final_url, resolved_identity=identity,
                       published=published, source_type=source_type, authority_tier=tier)

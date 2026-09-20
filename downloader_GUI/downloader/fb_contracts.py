# v14.2 Facebook contracts - immutable gallery plan finalization
"""Deterministic Facebook download pipeline contracts.

v13.1 keeps identity, gallery-count policy, account selection and exact-PCB
payload parsing separate from Playwright/DOM traversal.  These functions are
pure and regression-testable; they never touch browser, filesystem or network.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs, unquote
import html
import re


@dataclass(frozen=True)
class FacebookTarget:
    kind: str
    original_url: str
    numeric_id: str = ""
    story_fbid: str = ""
    post_id: str = ""
    share_token: str = ""


@dataclass(frozen=True)
class GalleryAudit:
    expected_count: int
    unique_output_count: int
    complete: bool
    reason: str


@dataclass(frozen=True)
class GalleryPlan:
    expected_count: int
    visible_grid_count: int
    plus_count: int
    scoped_link_count: int
    confidence: str
    reason: str



def finalize_gallery_plan(
    provisional_expected_count: int | None,
    visible_grid_count: int | None,
    plus_count: int | None,
    scoped_link_count: int | None,
    manifest_count: int | None = None,
    *,
    explicit_single: bool = False,
) -> GalleryPlan:
    """Finalize the Facebook gallery target exactly once.

    The +N overlay is painted on the final visible tile, therefore:
        total = (visible_grid_count - 1) + N

    Exact manifest evidence may prove a larger count. Raw scoped-link counts
    cannot raise a +N target because Facebook may expose post/container/ghost
    hrefs in the same scope.
    """
    def _n(value):
        try:
            return max(0, int(value or 0))
        except Exception:
            return 0

    provisional = _n(provisional_expected_count)
    grid = _n(visible_grid_count)
    plus = _n(plus_count)
    links = _n(scoped_link_count)
    manifest = _n(manifest_count)

    if explicit_single:
        return GalleryPlan(
            expected_count=1,
            visible_grid_count=grid,
            plus_count=plus,
            scoped_link_count=links,
            confidence="high",
            reason="explicit-single-media",
        )

    if grid > 0 and plus > 0:
        overlay_total = max(1, grid - 1) + plus
        expected = max(overlay_total, manifest)
        reason = "immutable-plus-overlay"
        if manifest > overlay_total:
            reason = "immutable-plus-overlay+manifest"
        return GalleryPlan(
            expected_count=expected,
            visible_grid_count=grid,
            plus_count=plus,
            scoped_link_count=links,
            confidence="high",
            reason=reason,
        )

    expected = max(provisional, manifest, links)
    if expected <= 0 and grid > 0:
        expected = grid

    return GalleryPlan(
        expected_count=expected,
        visible_grid_count=grid,
        plus_count=plus,
        scoped_link_count=links,
        confidence=("high" if manifest > 0 else ("medium" if links > 0 or grid > 0 else "low")),
        reason="immutable-exact-post-evidence",
    )


def parse_facebook_target(url: str) -> FacebookTarget:
    raw = str(url or "").strip()
    try:
        p = urlparse(raw)
        path = p.path or ""
        q = parse_qs(p.query or "")
    except Exception:
        path, q = raw, {}
    low = path.lower()
    story_fbid = (q.get("story_fbid") or [""])[0]
    post_id = (q.get("post_id") or [""])[0]
    numeric_id = ""
    share_token = ""
    kind = "unknown"

    m = re.search(r"/(?:reel|videos?)/(\d+)", path, re.I)
    if m:
        numeric_id = m.group(1)
        kind = "video"
    elif re.search(r"/share/[rv]/", low):
        kind = "video_share"
        m = re.search(r"/share/[rv]/([^/?#]+)", path, re.I)
        share_token = m.group(1) if m else ""
    elif re.search(r"/share/p/", low):
        kind = "post_share"
        m = re.search(r"/share/p/([^/?#]+)", path, re.I)
        share_token = m.group(1) if m else ""
    elif "story.php" in low or story_fbid:
        kind = "story_post"
    elif "/photo" in low:
        kind = "photo"
        numeric_id = (q.get("fbid") or [""])[0]
    elif "/watch" in low:
        kind = "video"
        numeric_id = (q.get("v") or [""])[0]
    return FacebookTarget(kind, raw, numeric_id, story_fbid, post_id, share_token)


def audit_gallery_completion(expected_count: int | None, unique_output_count: int | None) -> GalleryAudit:
    try:
        expected = max(0, int(expected_count or 0))
    except Exception:
        expected = 0
    try:
        unique = max(0, int(unique_output_count or 0))
    except Exception:
        unique = 0
    if expected <= 1:
        return GalleryAudit(expected, unique, True, "single-or-undeclared")
    if unique < expected:
        return GalleryAudit(expected, unique, False, "insufficient-unique-media")
    return GalleryAudit(expected, unique, True, "exact-or-complete")


def derive_gallery_plan(
    visible_grid_count: int | None,
    plus_count: int | None,
    scoped_link_count: int | None,
    manifest_count: int | None = None,
) -> GalleryPlan:
    """Derive one authoritative gallery target from exact-post evidence.

    v13.6 keeps one authoritative +N overlay contract end-to-end.

    Facebook paints ``+N`` on the LAST visible gallery tile.  N is the count
    represented by that overlay tile and the hidden continuation, so the total is:

        (visible_grid_count - 1) + N

    Examples:
      * five visible tiles with ``+3``  -> 4 + 3  = 7 total
      * five visible tiles with ``+12`` -> 4 + 12 = 16 total
      * five visible tiles with ``+76`` -> 4 + 76 = 80 total

    The previous v13.1 formula ``visible_grid_count + N`` was off by one and
    produced permanent N-1 retries such as 7/8 and 80/81.

    Exact-PCB link and manifest counts remain independent exact-post evidence
    and may raise the target when they prove more media identities.
    """
    def _n(v):
        try:
            return max(0, int(v or 0))
        except Exception:
            return 0

    grid = _n(visible_grid_count)
    plus = _n(plus_count)
    links = _n(scoped_link_count)
    manifest = _n(manifest_count)

    candidates: list[tuple[int, str]] = []
    if grid > 0:
        candidates.append((grid, "visible-grid"))
    if links > 0:
        candidates.append((links, "exact-pcb-links"))
    if manifest > 0:
        candidates.append((manifest, "manifest"))
    if grid > 0 and plus > 0:
        overlay_total = max(1, grid - 1) + plus
        candidates.append((overlay_total, "visible-grid-plus-overlay"))

    if not candidates:
        return GalleryPlan(0, grid, plus, links, "low", "no-gallery-count-evidence")

    expected, reason = max(candidates, key=lambda x: x[0])
    confidence = "high" if reason in {"visible-grid-plus-overlay", "manifest"} else "medium"
    return GalleryPlan(expected, grid, plus, links, confidence, reason)


def choose_post_account(*, group_root: str = "", page_or_author: str = "", fallback: str = "") -> str:
    """Stable GUI semantics: group post -> group/container name, else page/author.

    The GUI's Post Account represents the container/account the shared post
    belongs to.  For group shares that is the group root, not the member who
    authored the individual post.
    """
    for value in (group_root, page_or_author, fallback):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if value:
            return value
    return ""


def _decode_payload_text(text: str) -> str:
    s = html.unescape(str(text or ""))
    for _ in range(3):
        old = s
        s = (
            s.replace("\\/", "/")
            .replace("\\u002F", "/").replace("\\u002f", "/")
            .replace("\\u0026", "&")
            .replace("\\u003D", "=").replace("\\u003d", "=")
            .replace("\\u002E", ".").replace("\\u002e", ".")
            .replace("%3D", "=").replace("%3d", "=")
            .replace("%2E", ".").replace("%2e", ".")
            .replace("%26", "&")
        )
        try:
            s = unquote(s)
        except Exception:
            pass
        if s == old:
            break
    return s


def extract_exact_pcb_photo_ids(payload_texts: list[str] | tuple[str, ...] | None, pcb_id: str) -> list[str]:
    """Extract only photo IDs explicitly tied to set=pcb.<pcb_id>.

    This is intentionally strict.  Bare image/video IDs are ignored because a
    logged-in Facebook page also serializes recommendations and neighboring
    feed items.
    """
    pcb = re.sub(r"\D", "", str(pcb_id or ""))
    if not pcb:
        return []

    seen: set[str] = set()
    out: list[str] = []
    patterns = [
        rf"(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"'<>\s]{{0,1200}}?fbid=(\d{{8,}})[^\"'<>\s]{{0,1200}}?set=pcb\.{pcb}",
        rf"(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"'<>\s]{{0,1200}}?set=pcb\.{pcb}[^\"'<>\s]{{0,1200}}?fbid=(\d{{8,}})",
        rf"fbid=(\d{{8,}})(?:(?!https?://|fbid=|set=pcb\.).){{0,700}}?set=pcb\.{pcb}",
        rf"set=pcb\.{pcb}(?:(?!https?://|set=pcb\.|fbid=).){{0,700}}?fbid=(\d{{8,}})",
    ]

    for raw in payload_texts or []:
        text = _decode_payload_text(raw)
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.I | re.S):
                fbid = m.group(1)
                if fbid not in seen:
                    seen.add(fbid)
                    out.append(fbid)
    return out

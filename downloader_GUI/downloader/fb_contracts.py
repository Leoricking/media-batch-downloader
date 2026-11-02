# v14.7 Facebook contracts - generic share + canonical post identity
"""Deterministic Facebook download pipeline contracts."""
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
        return GalleryPlan(1, grid, plus, links, "high", "explicit-single-media")

    if grid > 0 and plus > 0:
        overlay_total = max(1, grid - 1) + plus
        expected = max(overlay_total, manifest)
        reason = "immutable-plus-overlay+manifest" if manifest > overlay_total else "immutable-plus-overlay"
        return GalleryPlan(expected, grid, plus, links, "high", reason)

    expected = max(provisional, manifest, links)
    if expected <= 0 and grid > 0:
        expected = grid

    return GalleryPlan(
        expected, grid, plus, links,
        ("high" if manifest > 0 else ("medium" if links > 0 or grid > 0 else "low")),
        "immutable-exact-post-evidence",
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

    m = re.search(r"/(?:reel|reels|videos?)/(\d+)", path, re.I)
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
    else:
        m_generic = re.search(r"/share/([^/?#]+)/?$", path, re.I)
        if m_generic and m_generic.group(1).lower() not in {"p", "v", "r"}:
            kind = "generic_share"
            share_token = m_generic.group(1)
        else:
            m_post = re.search(r"/[^/?#]+/posts/(\d{8,})(?:/|$)", path, re.I)
            if m_post:
                kind = "post_permalink"
                post_id = m_post.group(1)
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
    def _n(v):
        try:
            return max(0, int(v or 0))
        except Exception:
            return 0

    grid = _n(visible_grid_count)
    plus = _n(plus_count)
    links = _n(scoped_link_count)
    manifest = _n(manifest_count)
    candidates = []

    if grid > 0:
        candidates.append((grid, "visible-grid"))
    if links > 0:
        candidates.append((links, "exact-pcb-links"))
    if manifest > 0:
        candidates.append((manifest, "manifest"))
    if grid > 0 and plus > 0:
        candidates.append((max(1, grid - 1) + plus, "visible-grid-plus-overlay"))

    if not candidates:
        return GalleryPlan(0, grid, plus, links, "low", "no-gallery-count-evidence")

    expected, reason = max(candidates, key=lambda x: x[0])
    confidence = "high" if reason in {"visible-grid-plus-overlay", "manifest"} else "medium"
    return GalleryPlan(expected, grid, plus, links, confidence, reason)


def choose_post_account(*, group_root: str = "", page_or_author: str = "", fallback: str = "") -> str:
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


def extract_exact_pcb_photo_ids(payload_texts, pcb_id: str) -> list[str]:
    pcb = re.sub(r"\D", "", str(pcb_id or ""))
    if not pcb:
        return []

    seen = set()
    out = []
    patterns = [
        rf"(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"'<>\s]{{0,1200}}?fbid=(\d{{8,}})[^\"'<>\s]{{0,1200}}?set=pcb\.{pcb}",
        rf"(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"'<>\s]{{0,1200}}?set=pcb\.{pcb}[^\"'<>\s]{{0,1200}}?fbid=(\d{{8,}})",
        rf"fbid=(\d{{8,}})(?:(?!https?://|fbid=|set=pcb\.).){{0,700}}?set=pcb\.{pcb}",
        rf"set=pcb\.{pcb}(?:(?!https?://|set=pcb\.|fbid=).){{0,700}}?fbid=(\d{{8,}})",
    ]

    for raw in payload_texts or []:
        decoded = _decode_payload_text(raw)
        for pat in patterns:
            for m in re.finditer(pat, decoded, flags=re.I | re.S):
                fbid = m.group(1)
                if fbid not in seen:
                    seen.add(fbid)
                    out.append(fbid)
    return out

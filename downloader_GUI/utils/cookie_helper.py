"""
utils/cookie_helper.py

Shared Netscape cookies.txt -> Playwright cookie converter.
Use one cookies.txt for Instagram + Facebook.

Recommended export format: Netscape HTTP Cookie File
Browser extension example: "Get cookies.txt LOCALLY".
"""
from __future__ import annotations

import os
from typing import Dict, List


def load_netscape_cookies_to_playwright(
    file_path: str,
    domain_filter: str = "",
) -> List[Dict]:
    """
    Convert Netscape-format cookies.txt into Playwright context.add_cookies() format.

    Netscape columns:
      domain, include_subdomains, path, secure, expires, name, value

    Notes:
    - HttpOnly is not a Netscape column. Many exporters mark it as #HttpOnly_<domain>.
    - Playwright accepts cookies with domain/path/name/value and optional expires/secure/httpOnly.
    - We intentionally do not force sameSite because exported browser cookies often do not include it;
      forcing a wrong value can cause Playwright to reject or alter behavior.
    """
    cookies: List[Dict] = []

    if not file_path or not os.path.exists(file_path):
        return cookies

    normalized_filter = (domain_filter or "").strip().lower().lstrip(".")

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            http_only = False
            if line.startswith("#HttpOnly_"):
                line = line.replace("#HttpOnly_", "", 1)
                http_only = True
            elif line.startswith("#"):
                continue

            cols = line.split("\t")
            if len(cols) != 7:
                continue

            domain, _include_subdomains, path, secure, expires, name, value = cols
            clean_domain = (domain or "").strip()
            clean_domain_for_match = clean_domain.lower().lstrip(".")

            if normalized_filter and normalized_filter not in clean_domain_for_match:
                continue

            if not clean_domain or not name:
                continue

            cookie = {
                "name": name,
                "value": value,
                "domain": clean_domain,
                "path": path or "/",
                "secure": secure.upper() == "TRUE",
                "httpOnly": http_only,
            }

            try:
                exp = int(expires)
                if exp > 0:
                    cookie["expires"] = exp
            except Exception:
                pass

            cookies.append(cookie)

    return cookies

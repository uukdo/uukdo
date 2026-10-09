#!/usr/bin/env python3
"""Regenerate the Finder toolbar from languages used in owned GitHub repos."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from html import escape
from pathlib import Path


API_ROOT = "https://api.github.com"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "assets" / "finder-toolbar.svg"

# GitHub Linguist colors for commonly encountered languages. Unknown languages
# intentionally use a neutral color instead of inventing a misleading mapping.
LANGUAGE_COLORS = {
    "Assembly": "#6E4C13",
    "C": "#555555",
    "C#": "#178600",
    "C++": "#F34B7D",
    "CSS": "#663399",
    "Dart": "#00B4AB",
    "Dockerfile": "#384D54",
    "Elixir": "#6E4A7E",
    "Go": "#00ADD8",
    "HCL": "#844FBA",
    "HTML": "#E34C26",
    "Java": "#B07219",
    "JavaScript": "#F1E05A",
    "Jupyter Notebook": "#DA5B0B",
    "Kotlin": "#A97BFF",
    "Lua": "#000080",
    "Objective-C": "#438EFF",
    "PHP": "#4F5D95",
    "PLpgSQL": "#336790",
    "PowerShell": "#012456",
    "Python": "#3572A5",
    "R": "#198CE7",
    "Ruby": "#701516",
    "Rust": "#DEA584",
    "SCSS": "#C6538C",
    "Shell": "#89E051",
    "Solidity": "#AA6746",
    "Swift": "#F05138",
    "TypeScript": "#3178C6",
    "Vue": "#41B883",
}


def api_get(url: str, token: str) -> tuple[object, dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "uukdo-profile-language-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload, dict(response.headers.items())
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code >= 500 and attempt < 2:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"GitHub API returned {error.code}: {body}") from error
        except urllib.error.URLError as error:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"Could not reach the GitHub API: {error}") from error
    raise RuntimeError("GitHub API request failed")


def paginated_repositories(username: str, token: str, include_private: bool) -> list[dict]:
    repositories: list[dict] = []
    page = 1
    while True:
        if include_private:
            query = urllib.parse.urlencode(
                {
                    "affiliation": "owner",
                    "visibility": "all",
                    "sort": "full_name",
                    "per_page": 100,
                    "page": page,
                }
            )
            url = f"{API_ROOT}/user/repos?{query}"
        else:
            query = urllib.parse.urlencode(
                {"type": "owner", "sort": "full_name", "per_page": 100, "page": page}
            )
            url = f"{API_ROOT}/users/{urllib.parse.quote(username)}/repos?{query}"

        payload, _ = api_get(url, token)
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected repository response from GitHub")
        repositories.extend(payload)
        if len(payload) < 100:
            break
        page += 1

    username_folded = username.casefold()
    return [
        repo
        for repo in repositories
        if repo.get("owner", {}).get("login", "").casefold() == username_folded
        and not repo.get("fork", False)
    ]


def collect_languages(username: str, token: str, include_private: bool) -> tuple[Counter, int]:
    totals: Counter[str] = Counter()
    repositories = paginated_repositories(username, token, include_private)
    for repo in repositories:
        languages_url = repo.get("languages_url")
        if not languages_url:
            continue
        payload, _ = api_get(languages_url, token)
        if not isinstance(payload, dict):
            continue
        for language, byte_count in payload.items():
            if isinstance(byte_count, int) and byte_count > 0:
                totals[str(language)] += byte_count
    return totals, len(repositories)


def summarized_languages(totals: Counter[str]) -> list[dict[str, object]]:
    grand_total = sum(totals.values())
    if grand_total <= 0:
        raise RuntimeError("No language data was found in the selected repositories")

    ordered = totals.most_common()
    if len(ordered) <= 4:
        groups = ordered
    else:
        visible = ordered[:3]
        groups = visible + [("Other", sum(value for _, value in ordered[3:]))]

    result = []
    for language, byte_count in groups:
        result.append(
            {
                "name": language,
                "bytes": byte_count,
                "percent": byte_count / grand_total * 100,
                "color": "#8B949E" if language == "Other" else LANGUAGE_COLORS.get(language, "#8B949E"),
            }
        )
    return result


def render_svg(display_name: str, languages: list[dict[str, object]], repository_count: int) -> str:
    inner_x = 31.0
    inner_width = 196.0
    segments = []
    cursor = inner_x
    for index, language in enumerate(languages):
        if index == len(languages) - 1:
            width = inner_x + inner_width - cursor
        else:
            width = inner_width * float(language["percent"]) / 100
        segments.append(
            f'    <rect x="{cursor:.2f}" y="77" width="{max(width, 0):.2f}" height="20" '
            f'fill="{language["color"]}" />'
        )
        cursor += width

    legend_positions = [(270, 70), (466, 70), (270, 94), (466, 94)]
    legend = []
    for index, language in enumerate(languages[:4]):
        x, y = legend_positions[index]
        name = escape(str(language["name"]))
        value = float(language["percent"])
        legend.append(f'  <rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{language["color"]}" />')
        legend.append(f'  <text class="detail" x="{x + 16}" y="{y + 9}">{name} {value:.2f}%</text>')

    summary = ", ".join(
        f'{language["name"]} {float(language["percent"]):.2f}%' for language in languages
    )
    safe_display_name = escape(display_name)
    safe_desc = escape(
        f"Language usage across {repository_count} owned non-fork repositories. "
        f"{summary}."
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 126" role="img" aria-labelledby="title desc">
  <title id="title">{safe_display_name} Finder window header with live language battery</title>
  <desc id="desc">{safe_desc}</desc>
  <style>
    :root {{
      --chrome: #f2f2f4;
      --toolbar: #fafafa;
      --border: #d0d0d3;
      --input: #ffffff;
      --track: #e5e7eb;
      --text: #202124;
      --muted: #6e6e73;
      --accent: #0969da;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --chrome: #2b2b2d;
        --toolbar: #232325;
        --border: #444448;
        --input: #37373a;
        --track: #161b22;
        --text: #f5f5f7;
        --muted: #a1a1a6;
        --accent: #76f7ef;
      }}
    }}
    text {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif; fill: var(--text); }}
    .detail {{ font-size: 12px; fill: var(--muted); }}
  </style>

  <defs>
    <clipPath id="toolbar-battery-clip">
      <rect x="31" y="77" width="196" height="20" rx="5" />
    </clipPath>
    <linearGradient id="toolbar-shine" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".28" />
      <stop offset=".48" stop-color="#ffffff" stop-opacity="0" />
    </linearGradient>
  </defs>

  <path d="M18 0h964a18 18 0 0 1 18 18v108H0V18A18 18 0 0 1 18 0z" fill="var(--chrome)" />
  <path d="M0 54h1000v72H0z" fill="var(--toolbar)" />
  <path d="M0 54h1000M0 125h1000" stroke="var(--border)" />

  <circle cx="28" cy="27" r="8" fill="#ff5f57" />
  <circle cx="52" cy="27" r="8" fill="#febc2e" />
  <circle cx="76" cy="27" r="8" fill="#28c840" />
  <text x="500" y="34" text-anchor="middle" font-size="17" font-weight="600">{safe_display_name}</text>

  <rect x="24" y="70" width="210" height="34" rx="9" fill="var(--track)" stroke="var(--muted)" stroke-width="4" />
  <rect x="238" y="79" width="11" height="16" rx="3" fill="var(--muted)" />
  <g clip-path="url(#toolbar-battery-clip)">
{chr(10).join(segments)}
    <rect x="31" y="77" width="196" height="20" fill="url(#toolbar-shine)" />
  </g>

  <!-- Generated language status -->
{chr(10).join(legend)}

  <rect x="746" y="70" width="232" height="34" rx="10" fill="var(--input)" stroke="var(--accent)" stroke-width="2" />
  <circle cx="765" cy="86" r="6" fill="none" stroke="var(--text)" stroke-width="2" />
  <path d="m770 91 5 5" fill="none" stroke="var(--text)" stroke-width="2" stroke-linecap="round" />
  <text x="784" y="93" font-size="15">Welcome to {safe_display_name}</text>
  <path d="M922 79v16" stroke="var(--accent)" stroke-width="2">
    <animate attributeName="opacity" values="1;1;0;0;1" dur="1.1s" repeatCount="indefinite" />
  </path>
</svg>
'''


def main() -> int:
    username = os.getenv("GITHUB_REPOSITORY_OWNER", "UUKDO").strip()
    display_name = os.getenv("PROFILE_DISPLAY_NAME", "UUKDO").strip()
    token = os.getenv("STATS_TOKEN", os.getenv("GITHUB_TOKEN", "")).strip()
    include_private = os.getenv("INCLUDE_PRIVATE", "false").casefold() in {"1", "true", "yes"}

    if not username:
        print("GITHUB_REPOSITORY_OWNER is empty", file=sys.stderr)
        return 1
    if include_private and not token:
        print("INCLUDE_PRIVATE requires STATS_TOKEN", file=sys.stderr)
        return 1

    totals, repository_count = collect_languages(username, token, include_private)
    languages = summarized_languages(totals)
    svg = render_svg(display_name, languages, repository_count)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(svg, encoding="utf-8")

    scope = "public and accessible private" if include_private else "public"
    print(f"Updated {OUTPUT_PATH} from {repository_count} {scope} repositories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

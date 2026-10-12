#!/usr/bin/env python3
"""Regenerate the Finder toolbar from code changed in a user's commits."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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
    "SQL": "#E38C00",
    "Solidity": "#AA6746",
    "Swift": "#F05138",
    "TypeScript": "#3178C6",
    "Vue": "#41B883",
}

# This deliberately covers source-code formats rather than every extension in
# Linguist. Data, docs, images, lockfiles and generated project metadata should
# not outweigh the code a person actually changed.
EXTENSION_LANGUAGES = {
    ".asm": "Assembly",
    ".astro": "Astro",
    ".bash": "Shell",
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".cmake": "CMake",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".cxx": "C++",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".fs": "F#",
    ".fsx": "F#",
    ".go": "Go",
    ".gradle": "Groovy",
    ".graphql": "GraphQL",
    ".gql": "GraphQL",
    ".h": "C",
    ".hcl": "HCL",
    ".hh": "C++",
    ".hpp": "C++",
    ".html": "HTML",
    ".htm": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".mm": "Objective-C",
    ".mjs": "JavaScript",
    ".php": "PHP",
    ".pl": "Perl",
    ".proto": "Protocol Buffer",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sass": "Sass",
    ".scala": "Scala",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sol": "Solidity",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".tf": "HCL",
    ".tsx": "TypeScript",
    ".ts": "TypeScript",
    ".vue": "Vue",
    ".zig": "Zig",
}

FILENAME_LANGUAGES = {
    "cmakelists.txt": "CMake",
    "dockerfile": "Dockerfile",
    "gemfile": "Ruby",
    "makefile": "Makefile",
    "rakefile": "Ruby",
}

GENERATED_SUFFIXES = (".min.css", ".min.js", ".map")


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


def language_for_path(filename: str) -> str | None:
    """Return the source language for a changed path, or None for non-code."""

    normalized = filename.replace("\\", "/").casefold()
    basename = normalized.rsplit("/", 1)[-1]
    if basename.endswith(GENERATED_SUFFIXES):
        return None
    if basename in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[basename]
    if basename.endswith(".d.ts") or basename.endswith(".d.tsx"):
        return "TypeScript"
    if basename.endswith(".ipynb"):
        return "Jupyter Notebook"
    suffix = Path(basename).suffix
    return EXTENSION_LANGUAGES.get(suffix)


def _search_commit_range(
    username: str, token: str, start: date, end: date
) -> list[dict]:
    """Search one inclusive date range, splitting it at GitHub's 1,000 cap."""

    query = f"author:{username} author-date:{start.isoformat()}..{end.isoformat()}"
    params = {
        "q": query,
        "sort": "author-date",
        "order": "asc",
        "per_page": 100,
        "page": 1,
    }
    url = f"{API_ROOT}/search/commits?{urllib.parse.urlencode(params)}"
    payload, _ = api_get(url, token)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise RuntimeError("Unexpected commit search response from GitHub")
    if payload.get("incomplete_results"):
        raise RuntimeError("GitHub returned incomplete commit search results; try again later")

    total_count = int(payload.get("total_count", 0))
    if total_count > 1000:
        if start >= end:
            raise RuntimeError(
                f"More than 1,000 commits were found on {start.isoformat()}; "
                "use a shorter COMMIT_WINDOW_DAYS"
            )
        midpoint = start + (end - start) // 2
        return _search_commit_range(username, token, start, midpoint) + _search_commit_range(
            username, token, midpoint + timedelta(days=1), end
        )

    commits = list(payload["items"])
    pages = (total_count + 99) // 100
    for page in range(2, pages + 1):
        params["page"] = page
        page_url = f"{API_ROOT}/search/commits?{urllib.parse.urlencode(params)}"
        page_payload, _ = api_get(page_url, token)
        if not isinstance(page_payload, dict) or not isinstance(page_payload.get("items"), list):
            raise RuntimeError("Unexpected paginated commit search response from GitHub")
        if page_payload.get("incomplete_results"):
            raise RuntimeError("GitHub returned incomplete commit search results; try again later")
        commits.extend(page_payload["items"])
    return commits


def search_authored_commits(
    username: str, token: str, window_days: int, today: date | None = None
) -> list[dict]:
    """Find recent commits attributed to a GitHub user and remove duplicates."""

    end = today or datetime.now(timezone.utc).date()
    start = end - timedelta(days=window_days - 1)
    results = _search_commit_range(username, token, start, end)
    commits_by_sha: dict[str, dict] = {}
    for commit in results:
        sha = commit.get("sha")
        if not isinstance(sha, str) or not sha:
            continue
        # The same commit may be searchable through both an upstream repository
        # and a fork. Its SHA identifies the change, so count it only once.
        commits_by_sha.setdefault(sha, commit)
    return list(commits_by_sha.values())


def _commit_files(commit_url: str, token: str) -> list[dict]:
    """Fetch every changed file for a commit (GitHub paginates after 100)."""

    files: list[dict] = []
    for page in range(1, 31):
        separator = "&" if "?" in commit_url else "?"
        url = f"{commit_url}{separator}{urllib.parse.urlencode({'per_page': 100, 'page': page})}"
        payload, _ = api_get(url, token)
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            raise RuntimeError("Unexpected commit detail response from GitHub")
        page_files = payload["files"]
        files.extend(page_files)
        if len(page_files) < 100:
            return files
    raise RuntimeError("A commit changed more than GitHub's 3,000-file API limit")


def collect_languages(
    username: str, token: str, window_days: int
) -> tuple[Counter[str], int, int, int]:
    """Count changed source lines in the user's recent non-merge commits."""

    totals: Counter[str] = Counter()
    repositories: set[str] = set()
    code_commit_count = 0
    commits = search_authored_commits(username, token, window_days)

    for commit in commits:
        parents = commit.get("parents", [])
        if isinstance(parents, list) and len(parents) > 1:
            continue
        commit_url = commit.get("url")
        if not isinstance(commit_url, str) or not commit_url.startswith(f"{API_ROOT}/repos/"):
            continue

        commit_totals: Counter[str] = Counter()
        for changed_file in _commit_files(commit_url, token):
            filename = changed_file.get("filename")
            if not isinstance(filename, str):
                continue
            language = language_for_path(filename)
            if language is None:
                continue
            changes = changed_file.get("changes")
            if not isinstance(changes, int):
                additions = changed_file.get("additions", 0)
                deletions = changed_file.get("deletions", 0)
                changes = (
                    (additions if isinstance(additions, int) else 0)
                    + (deletions if isinstance(deletions, int) else 0)
                )
            if changes > 0:
                commit_totals[language] += changes

        if not commit_totals:
            continue
        totals.update(commit_totals)
        code_commit_count += 1
        repository = commit.get("repository", {})
        full_name = repository.get("full_name") if isinstance(repository, dict) else None
        if isinstance(full_name, str) and full_name:
            repositories.add(full_name.casefold())

    return totals, code_commit_count, len(repositories), sum(totals.values())


def summarized_languages(totals: Counter[str]) -> list[dict[str, object]]:
    grand_total = sum(totals.values())
    if grand_total <= 0:
        raise RuntimeError("No source-code changes were found in the selected commit window")

    ordered = totals.most_common()

    visible = []
    other_total = 0

    for language, change_count in ordered:
        percent = change_count / grand_total * 100

        if percent >= 5:
            visible.append((language, change_count))
        else:
            other_total += change_count

    if other_total > 0:
        visible.append(("Other", other_total))

    result = []
    for language, change_count in visible:
        result.append({
            "name": language,
            "changes": change_count,
            "percent": change_count / grand_total * 100,
            "color": (
                "#8B949E"
                if language == "Other"
                else LANGUAGE_COLORS.get(language, "#8B949E")
            ),
        })

    return result


def render_svg(
    display_name: str,
    languages: list[dict[str, object]],
    commit_count: int,
    repository_count: int,
    changed_lines: int,
    window_days: int,
) -> str:
    # --------------------------------------------------
    # Layout
    # --------------------------------------------------
    svg_width = 1100

    # Language legend
    legend_start_x = 365
    legend_start_y = 70
    legend_col_width = 215
    legend_row_height = 24
    legend_columns = 2

    legend_rows = (
        len(languages) + legend_columns - 1
    ) // legend_columns

    legend_height = legend_rows * legend_row_height
    legend_bottom = legend_start_y + legend_height

    # --------------------------------------------------
    # Battery
    # --------------------------------------------------
    battery_x = 24
    battery_width = 280
    battery_height = 68

    # Align battery vertically with the entire legend
    battery_y = (
        legend_start_y
        + (legend_height - battery_height) / 2
    )

    # Battery inner segments
    inner_padding = 7

    inner_x = battery_x + inner_padding
    inner_y = battery_y + inner_padding

    inner_width = battery_width - inner_padding * 2
    inner_height = battery_height - inner_padding * 2

    # Battery terminal
    terminal_width = 11
    terminal_height = 24

    terminal_x = battery_x + battery_width + 4
    terminal_y = (
        battery_y
        + (battery_height - terminal_height) / 2
    )

    # --------------------------------------------------
    # Finder search
    # --------------------------------------------------
    search_x = 846
    search_y = 70
    search_width = 232
    search_height = 34

    # --------------------------------------------------
    # Dynamic SVG height
    # --------------------------------------------------
    battery_bottom = battery_y + battery_height

    content_bottom = max(
        legend_bottom,
        battery_bottom,
        search_y + search_height,
    )

    meta_y = content_bottom + 18
    svg_height = max(126, meta_y + 15)

    # --------------------------------------------------
    # Battery segments
    # --------------------------------------------------
    segments = []
    cursor = inner_x

    for index, language in enumerate(languages):
        if index == len(languages) - 1:
            width = inner_x + inner_width - cursor
        else:
            width = (
                inner_width
                * float(language["percent"])
                / 100
            )

        segments.append(
            f'<rect x="{cursor:.2f}" y="{inner_y:.2f}" '
            f'width="{max(width, 0):.2f}" '
            f'height="{inner_height}" '
            f'fill="{language["color"]}" />'
        )

        cursor += width

    # --------------------------------------------------
    # Language legend
    # --------------------------------------------------
    legend = []

    for index, language in enumerate(languages):
        col = index % legend_columns
        row = index // legend_columns

        x = legend_start_x + col * legend_col_width
        y = legend_start_y + row * legend_row_height

        name = escape(str(language["name"]))
        value = float(language["percent"])

        legend.append(
            f'<rect x="{x}" y="{y}" '
            f'width="10" height="10" '
            f'rx="2" fill="{language["color"]}" />'
        )

        legend.append(
            f'<text class="detail" '
            f'x="{x + 16}" y="{y + 9}">'
            f'{name} {value:.2f}%</text>'
        )

    # --------------------------------------------------
    # Summary
    # --------------------------------------------------
    summary = ", ".join(
        f'{language["name"]} '
        f'{float(language["percent"]):.2f}%'
        for language in languages
    )

    safe_display_name = escape(display_name)

    safe_desc = escape(
        f"Language usage from {changed_lines:,} changed source lines "
        f"in {commit_count} non-merge commits across "
        f"{repository_count} repositories during the last "
        f"{window_days} days. {summary}."
    )

    safe_meta = escape(
        f"{changed_lines:,} changed lines · "
        f"{commit_count} commits · "
        f"{repository_count} repos · "
        f"{window_days}d"
    )

    search_text = escape(f"Welcome to {display_name}")

    # --------------------------------------------------
    # SVG
    # --------------------------------------------------
    return f'''<svg xmlns="http://www.w3.org/2000/svg"
  viewBox="0 0 {svg_width} {svg_height}"
  role="img"
  aria-labelledby="title desc">

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

    text {{
      font-family: -apple-system, BlinkMacSystemFont,
        "SF Pro Display", "Segoe UI", sans-serif;
      fill: var(--text);
    }}

    .detail {{
      font-size: 12px;
      fill: var(--muted);
    }}

    .meta {{
      font-size: 10px;
      fill: var(--muted);
    }}
  </style>

  <defs>
    <!-- Battery clipping -->
    <clipPath id="toolbar-battery-clip">
      <rect
        x="{inner_x}"
        y="{inner_y}"
        width="{inner_width}"
        height="{inner_height}"
        rx="8"
      />
    </clipPath>

    <!-- Battery shine -->
    <linearGradient id="toolbar-shine"
      x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity=".28" />
      <stop offset=".48" stop-color="#ffffff" stop-opacity="0" />
    </linearGradient>

    <!-- Search text clipping -->
    <clipPath id="finder-search-clip">
      <rect
        x="{search_x + 36}"
        y="{search_y + 3}"
        width="{search_width - 58}"
        height="28"
      />
    </clipPath>
  </defs>

  <!-- Finder window background -->
  <path
    d="M18 0
       H{svg_width - 18}
       A18 18 0 0 1 {svg_width} 18
       V{svg_height}
       H0
       V18
       A18 18 0 0 1 18 0
       Z"
    fill="var(--chrome)"
  />

  <!-- Toolbar background -->
  <path
    d="M0 54H{svg_width}V{svg_height}H0Z"
    fill="var(--toolbar)"
  />

  <!-- Toolbar borders -->
  <path
    d="M0 54H{svg_width}
       M0 {svg_height - 1}H{svg_width}"
    stroke="var(--border)"
  />

  <!-- Window controls -->
  <circle cx="28" cy="27" r="8" fill="#ff5f57" />
  <circle cx="52" cy="27" r="8" fill="#febc2e" />
  <circle cx="76" cy="27" r="8" fill="#28c840" />

  <!-- Window title -->
  <text
    x="{svg_width / 2}"
    y="34"
    text-anchor="middle"
    font-size="17"
    font-weight="600"
  >{safe_display_name}</text>

  <!-- Battery body -->
  <rect
    x="{battery_x}"
    y="{battery_y}"
    width="{battery_width}"
    height="{battery_height}"
    rx="12"
    fill="var(--track)"
    stroke="var(--muted)"
    stroke-width="4"
  />

  <!-- Battery terminal -->
  <rect
    x="{terminal_x}"
    y="{terminal_y}"
    width="{terminal_width}"
    height="{terminal_height}"
    rx="3"
    fill="var(--muted)"
  />

  <!-- Battery language segments -->
  <g clip-path="url(#toolbar-battery-clip)">
    {chr(10).join(segments)}

    <rect
      x="{inner_x}"
      y="{inner_y}"
      width="{inner_width}"
      height="{inner_height}"
      fill="url(#toolbar-shine)"
    />
  </g>

  <!-- Language legend -->
  {chr(10).join(legend)}

  <!-- Commit statistics -->
  <text
    class="meta"
    x="{legend_start_x}"
    y="{meta_y}"
  >{safe_meta}</text>

  <!-- Finder search field -->
  <rect
    x="{search_x}"
    y="{search_y}"
    width="{search_width}"
    height="{search_height}"
    rx="10"
    fill="var(--input)"
    stroke="var(--accent)"
    stroke-width="2"
  />

  <!-- Search icon -->
  <circle
    cx="{search_x + 19}"
    cy="{search_y + 16}"
    r="6"
    fill="none"
    stroke="var(--text)"
    stroke-width="2"
  />

  <path
    d="m{search_x + 24} {search_y + 21} 5 5"
    fill="none"
    stroke="var(--text)"
    stroke-width="2"
    stroke-linecap="round"
  />

  <!-- Search text -->
  <g clip-path="url(#finder-search-clip)">
    <text
      x="{search_x + 38}"
      y="{search_y + 23}"
      font-size="15"
    >{search_text}</text>
  </g>

  <!-- Blinking cursor -->
  <path
    d="M{search_x + search_width - 56} {search_y + 9}
       V{search_y + 25}"
    stroke="var(--accent)"
    stroke-width="2"
  >
    <animate
      attributeName="opacity"
      values="1;1;0;0;1"
      dur="1.1s"
      repeatCount="indefinite"
    />
  </path>

</svg>
'''


def main() -> int:
    username = os.getenv("GITHUB_REPOSITORY_OWNER", "UUKDO").strip()
    display_name = os.getenv("PROFILE_DISPLAY_NAME", "UUKDO").strip()
    token = os.getenv("STATS_TOKEN", os.getenv("GITHUB_TOKEN", "")).strip()
    window_days_raw = os.getenv("COMMIT_WINDOW_DAYS", "365").strip()

    if not username:
        print("GITHUB_REPOSITORY_OWNER is empty", file=sys.stderr)
        return 1
    if not token:
        print(
            "STATS_TOKEN is not set; continuing with public repository access",
            file=sys.stderr,
        )
    try:
        window_days = int(window_days_raw)
    except ValueError:
        print("COMMIT_WINDOW_DAYS must be an integer", file=sys.stderr)
        return 1
    if not 1 <= window_days <= 3660:
        print("COMMIT_WINDOW_DAYS must be between 1 and 3660", file=sys.stderr)
        return 1

    totals, commit_count, repository_count, changed_lines = collect_languages(
        username, token, window_days
    )
    languages = summarized_languages(totals)
    svg = render_svg(
        display_name,
        languages,
        commit_count,
        repository_count,
        changed_lines,
        window_days,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(svg, encoding="utf-8")

    print(
        f"Updated {OUTPUT_PATH} from {changed_lines:,} changed lines in "
        f"{commit_count} commits across {repository_count} repositories"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

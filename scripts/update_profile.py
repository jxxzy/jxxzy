#!/usr/bin/env python3
"""Refresh only the marked public repository index in the profile README."""
import html
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET
from urllib.error import HTTPError, URLError
from pathlib import Path
from urllib.request import Request, urlopen

START = "<!-- AUTO:PUBLIC-REPOSITORIES:START -->"
END = "<!-- AUTO:PUBLIC-REPOSITORIES:END -->"
ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_REPO_ID = 760556898

def request_json(url, headers):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** attempt)


def fetch_public(owner, kind):
    result = {}
    page = 1
    while True:
        url = (f"https://api.github.com/{kind}/{owner}/repos"
               f"?type=public&per_page=100&page={page}&sort=full_name")
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "jxxzy-profile-refresh"}
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
        batch = request_json(url, headers)
        if not isinstance(batch, list):
            raise ValueError("Unexpected API response")
        for repo in batch:
            if (repo.get("private") is False
                and repo.get("visibility") == "public"
                and repo["owner"]["login"].casefold() == owner.casefold()):
                result[repo["id"]] = repo
        if len(batch) < 100:
            break
        page += 1
    if not result:
        raise ValueError(f"No public repositories returned for {owner}; refusing to overwrite")
    return sorted(result.values(), key=lambda r: r["name"].casefold())

def cell(value):
    # Escape API-provided strings so they cannot inject Markdown/HTML.
    value = " ".join(str(value or "—").split())
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()|#!~])", r"\\\1", value)

def link(repo):
    full_name = repo["full_name"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise ValueError("Unexpected repository name")
    return f"[{cell(repo['name'])}](https://github.com/{full_name})"

def catalog(title, repos):
    rows = ["<details open>", f"<summary><b>{title} ({len(repos)})</b></summary>", "",
            "| Repository | Language | Type | Stars | Last push (UTC) |",
            "| :--- | :--- | :--- | ---: | :--- |"]
    for r in repos:
        kind = "Fork" if r["fork"] else "Non-fork"
        if r["archived"]:
            kind += " / Archived"
        pushed = r.get("pushed_at") or ""
        # Avoid self-triggered changes from this README's own automation commits.
        date = "—" if r["id"] == PORTFOLIO_REPO_ID else (pushed[:10] or "—")
        rows.append(f"| {link(r)} | {cell(r.get('language'))} | {kind} | {int(r['stargazers_count'])} | {cell(date)} |")
    return "\n".join(rows + ["", "</details>"])

def recent_activity(personal, organization):
    eligible = [r for r in personal + organization
                if not r["fork"] and not r["archived"]
                and r["id"] != PORTFOLIO_REPO_ID and r.get("pushed_at")]
    recent = sorted(eligible, key=lambda r: (r["pushed_at"], r["full_name"].casefold()), reverse=True)[:8]
    rows = ["### Recently pushed public projects", "",
            "| Repository | Language | Last push (UTC) | Stars |",
            "| :--- | :--- | :--- | ---: |"]
    for r in recent:
        rows.append(f"| {link(r)} | {cell(r.get('language'))} | {cell(r['pushed_at'][:10])} | {int(r['stargazers_count'])} |")
    if not recent:
        rows.append("| — | — | — | — |")
    return "\n".join(rows)


def render(personal, organization):
    forks = sum(bool(r["fork"]) for r in personal)
    top = ["| Personal public | Personal non-fork | Personal forks | NEXORAID public |",
           "| :---: | :---: | :---: | :---: |",
           f"| **{len(personal)}** | **{len(personal)-forks}** | **{forks}** | **{len(organization)}** |",
           "",
           "Public metadata from the GitHub REST API. Non-fork does not imply original authorship.",
           "",
           f"**Public repository stars:** {sum(int(r['stargazers_count']) for r in personal)} personal · {sum(int(r['stargazers_count']) for r in organization)} NEXORAID (including forks).", "",
           recent_activity(personal, organization), "",
           catalog("Personal repositories", personal), "",
           catalog("NEXORAID repositories", organization), "",
           "<sub>Scheduled refresh every six hours; commits are made only when the displayed data changes. This portfolio repository's push date is omitted to prevent self-triggered changes.</sub>"]
    return "\n".join(top)

def analytics_svg(personal, organization):
    repos = personal + organization
    eligible = [r for r in repos if not r["fork"] and not r["archived"]
                and r["id"] != PORTFOLIO_REPO_ID]
    languages = Counter(r.get("language") or "Unspecified" for r in eligible)
    top = sorted(languages.items(), key=lambda item: (-item[1], item[0].casefold()))[:6]
    stats = [("PERSONAL PUBLIC", len(personal)), ("NEXORAID PUBLIC", len(organization)),
             ("PUBLIC STARS", sum(int(r["stargazers_count"]) for r in repos)),
             ("PERSONAL FORKS", sum(bool(r["fork"]) for r in personal))]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="350" viewBox="0 0 900 350" role="img">',
           '<title>JXXZY public repository analytics</title>',
           '<desc>GitHub API public repository counts, stars, forks and most common primary languages.</desc>',
           '<rect x="1" y="1" width="898" height="348" rx="18" fill="#101827" stroke="#334155"/>',
           '<g font-family="system-ui, sans-serif">',
           '<text x="28" y="36" font-size="21" font-weight="700" fill="#e2e8f0">JXXZY / PUBLIC REPOSITORY ANALYTICS</text>']
    for index, (label, value) in enumerate(stats):
        x = 28 + 215 * index
        out.extend([f'<rect x="{x}" y="55" width="199" height="78" rx="10" fill="#1e293b"/>',
                    f'<text x="{x+12}" y="77" font-size="11" fill="#94a3b8">{label}</text>',
                    f'<text x="{x+12}" y="112" font-size="28" font-weight="700" fill="#67e8f9">{value}</text>'])
    out.append('<text x="28" y="163" font-size="15" font-weight="600" fill="#e2e8f0">Primary languages by repository count</text>')
    maximum = max((count for _, count in top), default=1)
    for index, (lang, count) in enumerate(top):
        y = 187 + index * 24
        safe = html.escape(lang[:24], quote=False)
        width = round(530 * count / maximum)
        out.extend([f'<text x="28" y="{y+11}" font-size="12" fill="#cbd5e1">{safe}</text>',
                    f'<rect x="235" y="{y}" width="530" height="12" rx="6" fill="#1e293b"/>',
                    f'<rect x="235" y="{y}" width="{width}" height="12" rx="6" fill="#8b5cf6"/>',
                    f'<text x="795" y="{y+11}" font-size="12" fill="#e2e8f0">{count}</text>'])
    if not top:
        out.append('<text x="28" y="202" fill="#94a3b8" font-size="12">No eligible public repositories</text>')
    out.append('<text x="28" y="337" fill="#94a3b8" font-size="11">Source: GitHub REST API | Public metadata only | No estimated contribution counts</text>')
    out.append('</g></svg>')
    svg = "\n".join(out) + "\n"
    ET.fromstring(svg)
    return svg


def validate_assets():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for asset in set(re.findall(r'<img src="(assets/[^"]+)"', readme)):
        path = ROOT / asset
        ET.parse(path)
    print("Local SVG assets validated")


def probe_external():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("Additional live contribution cards", 1)[1].split("</details>", 1)[0]
    urls = [html.unescape(url) for url in re.findall(r'<img src="(https://[^"]+)"', section)]
    def probe(url):
        try:
            # No GitHub token is sent to external image services.
            with urlopen(Request(url, headers={"User-Agent": "jxxzy-profile-health"}), timeout=8) as response:
                body = response.read(2_000_001)
                kind = response.headers.get("Content-Type", "")
                if len(body) > 2_000_000:
                    return url, "unverified: response too large"
                if "svg" in kind.lower():
                    ET.fromstring(body)
                    if re.search(rb"rate limit|something went wrong|could not fetch|error fetching|application error|api error", body, re.I):
                        return url, "provider error indicator"
                    return url, "valid SVG response"
                return url, "HTTP success: " + kind
        except Exception as exc:
            return url, "unavailable: " + type(exc).__name__
    with ThreadPoolExecutor(max_workers=6) as pool:
        for url, status in pool.map(probe, urls):
            print(status + " | " + url)


def replace_index(original, generated):
    if original.count(START) != 1 or original.count(END) != 1:
        raise ValueError("Expected exactly one marker pair")
    before, tail = original.split(START, 1)
    if END not in tail:
        raise ValueError("Markers out of order")
    _, after = tail.split(END, 1)
    if START in generated or END in generated:
        raise ValueError("Unexpected generated markers")
    return before + START + "\n" + generated + "\n" + END + after

def self_test():
    original = "manual before\n" + START + "\nold\n" + END + "\nmanual after\n"
    updated = replace_index(original, "new")
    assert updated == original.replace("\nold\n", "\nnew\n")
    assert replace_index(updated, "new") == updated
    for bad in ("missing", END + START, START + START + END):
        try:
            replace_index(bad, "new")
        except ValueError:
            continue
        raise AssertionError("Malformed markers accepted")
    fixture = dict(id=PORTFOLIO_REPO_ID, name="jxxzy", full_name="jxxzy/jxxzy", fork=False, archived=False,
                   language="Markdown", stargazers_count=0, pushed_at="2026-11-26T00:00:00Z")
    assert "2026-11-26" not in render([fixture], [fixture])
    assert "&#" in cell("<script>") or "&lt;" in cell("<script>")
    project = dict(fixture, id=123, name="example", full_name="jxxzy/example", stargazers_count=4)
    fork = dict(project, id=124, name="fork", full_name="jxxzy/fork", fork=True)
    archived = dict(project, id=125, name="archive", full_name="jxxzy/archive", archived=True)
    recent = recent_activity([fixture, project, fork, archived], [])
    assert "jxxzy/example" in recent
    assert "jxxzy/jxxzy)" not in recent and "jxxzy/fork" not in recent and "jxxzy/archive" not in recent
    rendered = render([fixture, project, fork], [])
    assert "**3** | **2** | **1** | **0**" in rendered
    assert "<details open>" in rendered and "Public repository stars" in rendered
    assert replace_index(replace_index(original, rendered), rendered) == replace_index(original, rendered)
    svg = analytics_svg([fixture, project, fork, archived], [])
    assert ET.fromstring(svg).tag.endswith("svg")
    assert svg == analytics_svg([fixture, project, fork, archived], [])
    assert "PUBLIC STARS" in svg and "PERSONAL FORKS" in svg
    print("Self-test passed")

def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    if "--check-assets" in sys.argv:
        validate_assets()
        return
    if "--probe-external" in sys.argv:
        probe_external()
        return
    path = ROOT / "README.md"
    original = path.read_bytes().decode("utf-8")
    personal, organization = fetch_public("jxxzy", "users"), fetch_public("NEXORAID-ID", "orgs")
    generated = render(personal, organization)
    svg = analytics_svg(personal, organization)
    asset = ROOT / "assets" / "profile-analytics.svg"
    if not asset.exists() or asset.read_bytes() != svg.encode("utf-8"):
        asset.write_bytes(svg.encode("utf-8"))
        print("Updated local analytics SVG")
    updated = replace_index(original, generated)
    if updated != original:
        path.write_bytes(updated.encode("utf-8"))
        print("Updated public index; manual sections unchanged")
    else:
        print("No metadata changes")

if __name__ == "__main__":
    main()

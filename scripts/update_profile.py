#!/usr/bin/env python3
"""Refresh only the marked public repository index in the profile README."""
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

START = "<!-- AUTO:PUBLIC-REPOSITORIES:START -->"
END = "<!-- AUTO:PUBLIC-REPOSITORIES:END -->"
ROOT = Path(__file__).resolve().parents[1]
PROFILE = "jxxzy/JXXZY"

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
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            batch = json.load(response)
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
    rows = ["<details>", f"<summary><b>{title} ({len(repos)})</b></summary>", "",
            "| Repository | Language | Type | Stars | Last push (UTC) |",
            "| :--- | :--- | :--- | ---: | :--- |"]
    for r in repos:
        kind = "Fork" if r["fork"] else "Non-fork"
        if r["archived"]:
            kind += " / Archived"
        pushed = r.get("pushed_at") or ""
        # Avoid self-triggered changes from this README's own automation commits.
        date = "—" if r["full_name"].casefold() == PROFILE.casefold() else (pushed[:10] or "—")
        rows.append(f"| {link(r)} | {cell(r.get('language'))} | {kind} | {int(r['stargazers_count'])} | {cell(date)} |")
    return "\n".join(rows + ["", "</details>"])

def render(personal, organization):
    forks = sum(bool(r["fork"]) for r in personal)
    top = ["| Personal public | Personal non-fork | Personal forks | NEXORAID public |",
           "| :---: | :---: | :---: | :---: |",
           f"| **{len(personal)}** | **{len(personal)-forks}** | **{forks}** | **{len(organization)}** |",
           "",
           "Public metadata from the GitHub REST API. Non-fork does not imply original authorship.",
           "",
           catalog("Personal repositories", personal), "",
           catalog("NEXORAID repositories", organization), "",
           "<sub>Checked daily; commits are made only when the displayed data changes. The profile repository's push date is omitted to prevent self-triggered changes.</sub>"]
    return "\n".join(top)

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
    fixture = dict(name="JXXZY", full_name=PROFILE, fork=False, archived=False,
                   language="Markdown", stargazers_count=0, pushed_at="2026-11-26T00:00:00Z")
    assert "2026-11-26" not in render([fixture], [fixture])
    assert "&#" in cell("<script>") or "&lt;" in cell("<script>")
    print("Self-test passed")

def main():
    if "--self-test" in sys.argv:
        self_test()
        return
    path = ROOT / "README.md"
    original = path.read_bytes().decode("utf-8")
    generated = render(fetch_public("jxxzy", "users"), fetch_public("NEXORAID-ID", "orgs"))
    updated = replace_index(original, generated)
    if updated != original:
        path.write_bytes(updated.encode("utf-8"))
        print("Updated public index; manual sections unchanged")
    else:
        print("No metadata changes")

if __name__ == "__main__":
    main()

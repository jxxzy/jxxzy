#!/usr/bin/env python3
"""Keep visual features available through provider outages; never invent data."""
import base64
import concurrent.futures
import os
import html
import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "assets" / "provider-cache"
MANIFEST = ROOT / "scripts" / "profile_providers.json"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
MAX = 2_000_000
ERROR = re.compile(r"rate limit|something went wrong|could not fetch|error fetching|application error|api error|maximum retries|could not resolve|service unavailable|bad gateway|an error occurred|invalid username|request failed|unable to fetch|no stats available", re.I)
ALLOWED = {"github-readme-stats.vercel.app", "streak-stats.demolab.com", "github-profile-summary-cards.vercel.app", "github-readme-activity-graph.vercel.app", "skillicons.dev", "img.shields.io"}

def normalize(body, depth=0):
    if depth > 2 or len(body) > MAX or re.search(rb"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", body, re.I):
        raise ValueError("unsafe or oversized response")
    root = ET.fromstring(body)
    if root.tag != "{" + NS + "}svg":
        raise ValueError("not SVG")
    text = " ".join(" ".join(node.itertext()) for node in root.iter() if node.tag.rsplit("}", 1)[-1] in ("text", "title", "desc", "tspan"))
    if ERROR.search(text):
        raise ValueError("provider error indicator")
    if not root.get("viewBox"):
        dims = []
        for key in ("width", "height"):
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(?:px)?", root.get(key, ""))
            if not match or float(match[1]) <= 0:
                raise ValueError("missing SVG dimensions")
            dims.append(match[1])
        root.set("viewBox", "0 0 " + " ".join(dims))
    ids = [node.get("id") for node in root.iter() if node.get("id")]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate SVG IDs")
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("script", "foreignObject", "iframe", "audio", "video"):
            raise ValueError("unsafe SVG element")
        for key, value in list(node.attrib.items()):
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on"):
                raise ValueError("unsafe SVG event handler")
            if name in ("href", "src") and not value.startswith("#"):
                if tag == "a" and value.startswith("https://"):
                    # README already has the intended link; strip nested external link.
                    del node.attrib[key]
                    continue
                if tag == "image" and value.startswith("data:image/svg+xml;base64,"):
                    embedded = base64.b64decode(value.split(",", 1)[1], validate=True)
                    safe = normalize(embedded, depth+1)
                    node.set(key, "data:image/svg+xml;base64," + base64.b64encode(safe).decode("ascii"))
                elif tag == "image" and re.match(r"data:image/svg\+xml(?:;utf8|;charset=utf-8)?,", value, re.I):
                    from urllib.parse import unquote_to_bytes
                    safe = normalize(unquote_to_bytes(value.split(",", 1)[1]), depth+1)
                    node.set(key, "data:image/svg+xml;base64," + base64.b64encode(safe).decode("ascii"))
                else:
                    raise ValueError("external SVG reference")
            for target in re.findall(r"url\(#([^)]*)\)", value):
                if target not in ids:
                    raise ValueError("missing SVG reference")
        css = (node.text or "") if tag == "style" else node.get("style", "")
        if re.search(r"@import", css, re.I) or any(not u.strip().strip("\"'").startswith("#") for u in re.findall(r"url\(([^)]*)\)", css, re.I)):
            raise ValueError("external CSS reference")
    return ET.tostring(root, encoding="utf-8") + b"\n"

def fallback(item):
    label = html.escape(item["label"])
    if item["kind"] == "badge":
        width = max(100, min(300, len(item["label"]) * 9 + 35))
        return f'<svg xmlns="{NS}" width="{width}" height="28" viewBox="0 0 {width} 28"><title>{label}</title><rect width="{width}" height="28" rx="4" fill="#334155"/><text x="{width/2}" y="19" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#fff">{label}</text></svg>\n'.encode()
    if item["kind"] == "skills":
        parts = item["label"].split(": ", 1)[-1].split(", ")
        rows = []
        for i, name in enumerate(parts):
            x, y = 10 + i % 4 * 180, 15 + i // 4 * 44
            rows.append(f'<rect x="{x}" y="{y}" width="170" height="34" rx="7" fill="#1e293b"/><text x="{x+85}" y="{y+22}" text-anchor="middle" fill="#e2e8f0" font-size="13" font-family="sans-serif">{html.escape(name)}</text>')
        h = 20 + ((len(parts)+3)//4)*44
        return (f'<svg xmlns="{NS}" width="730" height="{h}" viewBox="0 0 730 {h}"><title>{label} — text fallback</title>' + "".join(rows) + "</svg>\n").encode()
    return f'<svg xmlns="{NS}" width="700" height="150" viewBox="0 0 700 150"><title>{label}</title><rect x="1" y="1" width="698" height="148" rx="12" fill="#1a1b26" stroke="#7aa2f7"/><text x="24" y="45" fill="#7aa2f7" font-size="20" font-family="sans-serif">{label}</text><text x="24" y="82" fill="#c0caf5" font-size="15" font-family="sans-serif">Live provider temporarily unavailable; no verified snapshot yet.</text><text x="24" y="112" fill="#a9b1d6" font-size="14" font-family="sans-serif">See native GitHub profile for current activity.</text></svg>\n'.encode()

def fetch(item):
    from urllib.parse import urlparse
    url = item["url"]
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in ALLOWED:
        raise ValueError("unexpected provider")
    with urlopen(Request(url, headers={"User-Agent": "jxxzy-profile-cache", "Accept": "image/svg+xml"}), timeout=15) as response:
        if urlparse(response.geturl()).scheme != "https" or urlparse(response.geturl()).hostname not in ALLOWED:
            raise ValueError("unexpected redirect")
        if "svg" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("unexpected content type")
        return normalize(response.read(MAX+1))

def native_activity():
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GitHub token unavailable")
    query = """query { user(login: "jxxzy") { contributionsCollection { contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } } } }"""
    request = Request("https://api.github.com/graphql", data=json.dumps({"query": query}).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "jxxzy-profile-cache"})
    with urlopen(request, timeout=20) as response:
        result = json.load(response)
    if result.get("errors"):
        raise ValueError("GitHub contribution query failed")
    return render_activity(result["data"]["user"]["contributionsCollection"]["contributionCalendar"])

def render_activity(calendar):
    weeks = calendar["weeks"]
    if not weeks or len(weeks) > 54:
        raise ValueError("invalid contribution calendar")
    days = [d for week in weeks for d in week["contributionDays"]]
    if not days or any(not isinstance(d["contributionCount"], int) or d["contributionCount"] < 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]) for d in days):
        raise ValueError("invalid contribution values")
    totals = [sum(d["contributionCount"] for d in week["contributionDays"]) for week in weeks]
    total = int(calendar["totalContributions"])
    if total != sum(totals):
        raise ValueError("inconsistent contribution calendar")
    maximum = max(1, *totals)
    points = [(46 + 808*i/max(1,len(totals)-1), 181 - 112*v/maximum) for i,v in enumerate(totals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x,y in points)
    area = f"{points[0][0]:.1f},181 " + line + f" {points[-1][0]:.1f},181"
    first, last = days[0]["date"], days[-1]["date"]
    return normalize(f'<svg xmlns="{NS}" width="900" height="240" viewBox="0 0 900 240"><title>GitHub contribution activity graph</title><rect x="1" y="1" width="898" height="238" rx="12" fill="#1a1b26" stroke="#394b70"/><text x="30" y="32" font-family="sans-serif" font-size="20" fill="#7aa2f7" font-weight="bold">JXXZY / GitHub contribution activity</text><text x="30" y="54" font-family="sans-serif" font-size="13" fill="#c0caf5">{total} visible contributions · weekly totals · GitHub GraphQL</text><path d="M46 69 H854 M46 125 H854 M46 181 H854" stroke="#394b70" fill="none"/><polygon points="{area}" fill="#7aa2f7" opacity=".18"/><polyline points="{line}" stroke="#7aa2f7" stroke-width="2" fill="none" stroke-linejoin="round"/><text x="46" y="202" fill="#c0caf5" font-family="sans-serif" font-size="12">{first}</text><text x="854" y="202" text-anchor="end" fill="#c0caf5" font-family="sans-serif" font-size="12">{last}</text><text x="30" y="225" fill="#a9b1d6" font-family="sans-serif" font-size="11">Source: official GitHub contribution calendar · activity visible to the workflow token.</text></svg>'.encode())

def native_badge(item):
    from update_profile import request_json, fetch_public
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "jxxzy-profile-cache"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    key = item["name"]
    if key == "badge-5":
        count = request_json("https://api.github.com/repos/jxxzy/HyperBoostX", headers)["stargazers_count"]
        label, color, height = "stars", "#22d3ee", 20
    elif key == "badge-7":
        count = request_json("https://api.github.com/users/jxxzy", headers)["followers"]
        label, color, height = "FOLLOWERS", "#0891b2", 28
    elif key == "badge-8":
        count = sum(int(repo["stargazers_count"]) for repo in fetch_public("jxxzy", "users"))
        label, color, height = "STARS", "#7c3aed", 28
    else:
        raise ValueError("not a supported native badge")
    if not isinstance(count, int) or count < 0:
        raise ValueError("invalid badge count")
    logo = ""
    if height == 28:
        path = CACHE / "badge-1.svg"
        if path.exists():
            svg = ET.fromstring(normalize(path.read_bytes()))
            for node in svg.iter():
                if node.tag == "{" + NS + "}image" and node.get("href", "").startswith("data:image/svg+xml;base64,"):
                    logo = f'<image x="9" y="7" width="14" height="14" href="{node.get("href")}"/>'
                    break
    offset = 23 if logo else 0
    left = len(label)*8 + 20 + offset
    right = max(32, len(str(count))*8+20)
    width = left + right
    font = 12 if height == 20 else 11
    y = height/2 + 4
    ink = "#13212a" if key == "badge-5" else "#fff"
    return normalize(f'<svg xmlns="{NS}" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img"><title>{label}: {count} (GitHub REST API)</title><rect width="{left}" height="{height}" fill="#555"/><rect x="{left}" width="{right}" height="{height}" fill="{color}"/>{logo}<g font-family="Verdana,sans-serif" font-size="{font}" text-anchor="middle"><text x="{(left+offset)/2}" y="{y}" fill="#fff">{label}</text><text x="{left+right/2}" y="{y}" fill="{ink}">{count}</text></g></svg>'.encode())

def read_state():
    path = CACHE / "status.json"
    return json.loads(path.read_text()) if path.exists() else {}

def local_path(item):
    return CACHE / (item["name"] + ".svg")

def refresh_one(item, previous):
    path = local_path(item)
    try:
        body = fetch(item)
        status = "verified"
    except Exception as exc:
        print("::warning::" + item["name"] + ": " + type(exc).__name__ + " " + str(exc)[:120])
        native = None
        if item["name"] == "activity-graph":
            try:
                native = native_activity()
            except Exception as error:
                print("::warning::native activity: " + type(error).__name__ + " " + str(error)[:100])
        elif item["name"] in ("badge-5", "badge-7", "badge-8"):
            try:
                native = native_badge(item)
            except Exception as error:
                print("::warning::native badge: " + type(error).__name__ + " " + str(error)[:100])
        if native is not None:
            body, status = native, "verified-github-native"
        elif previous.get(item["name"]) in ("verified", "last-known-good", "verified-github-native") and path.exists():
            try:
                body = normalize(path.read_bytes())
                status = "last-known-good"
            except Exception:
                body, status = fallback(item), "fallback"
        else:
            body, status = fallback(item), "fallback"
    if not path.exists() or path.read_bytes() != body:
        path.write_bytes(body)
    print(item["name"] + ": " + status)
    return item["name"], status

def replace_urls(readme, items):
    for item in items:
        local = "assets/provider-cache/" + item["name"] + ".svg"
        for source in (item["url"], item["url"].replace("&", "&amp;")):
            readme = readme.replace('src="' + source + '"', 'src="' + local + '"')
            readme = readme.replace("](" + source + ")", "](" + local + ")")
        if local not in readme:
            raise ValueError("visual missing from README: " + item["name"])
    return readme

def main():
    items = json.loads(MANIFEST.read_text())
    if len({i["name"] for i in items}) != len(items):
        raise ValueError("duplicate provider names")
    if "--self-test" in sys.argv:
        valid = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="20"><text>OK</text></svg>'
        assert b'viewBox="0 0 10 20"' in normalize(valid)
        for invalid in (b"<html/>", valid.replace(b"OK", b"rate limit"), valid.replace(b"<text>", b'<text onclick="bad()">'), valid.replace(b"<text>", b'<text style="fill:url(https://bad.invalid)">')):
            try:
                normalize(invalid)
            except Exception:
                continue
            raise AssertionError("invalid response accepted")
        for item in items:
            normalize(fallback(item))
        embedded = base64.b64encode(valid).decode()
        logo = f'<svg xmlns="{NS}" width="10" height="20"><image href="data:image/svg+xml;base64,{embedded}"/></svg>'.encode()
        assert b"data:image/svg+xml;base64," in normalize(logo)
        assert b"https://example.com" not in normalize(valid.replace(b"<text>OK</text>", b'<a href="https://example.com"><text>OK</text></a>'))
        sample = {"totalContributions": 3, "weeks": [{"contributionDays": [{"date": "2026-01-01", "contributionCount": 3}]}]}
        assert b"3 visible contributions" in render_activity(sample)
        print("Provider cache self-tests passed")
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    previous = read_state()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        state = dict(pool.map(lambda item: refresh_one(item, previous), items))
    (CACHE / "status.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    path = ROOT / "README.md"
    original = path.read_text()
    updated = replace_urls(original, items)
    if updated != original:
        path.write_text(updated)
    for item in items:
        normalize(local_path(item).read_bytes())
    print("Provider cache validated:", len(items), "visuals")

if __name__ == "__main__":
    main()

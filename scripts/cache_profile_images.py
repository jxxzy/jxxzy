#!/usr/bin/env python3
"""Keep visual features available through provider outages; never invent data."""
import concurrent.futures
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

def normalize(body):
    if len(body) > MAX or re.search(rb"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", body, re.I):
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
        for key, value in node.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (name in ("href", "src") and not value.startswith("#")):
                raise ValueError("external SVG reference")
            for target in re.findall(r"url\(#([^)]*)\)", value):
                if target not in ids:
                    raise ValueError("missing SVG reference")
        css = (node.text or "") if tag == "style" else node.get("style", "")
        if re.search(r"@import|url\s*\(\s*['\"]?(?!#)[^)]", css, re.I):
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
        if previous.get(item["name"]) in ("verified", "last-known-good") and path.exists():
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

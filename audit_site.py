#!/usr/bin/env python3
"""Production crawl audit for yishunagain.com; stdlib only."""

import html.parser
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

CANONICAL = "https://www.yishunagain.com"
BAD_PATHS = {"/$", "/&"}
TIMEOUT = 12


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
        self.canonicals = []
        self.noindex = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
        if tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonicals.append(values.get("href", ""))
        if tag == "meta" and values.get("name", "").lower() == "robots":
            self.noindex |= "noindex" in values.get("content", "").lower()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "yishunagain-crawl-audit/1.0"})
    try:
        with OPENER.open(request, timeout=TIMEOUT) as response:
            return response.status, response.geturl(), response.read()
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location")
        final = urllib.parse.urljoin(error.geturl(), location) if location else error.geturl()
        return error.code, final, error.read()


def main():
    checks = []
    sitemap_ok = False

    def record(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        status, final, body = fetch(f"{CANONICAL}/sitemap.xml")
        sitemap_ok = status == 200 and final == f"{CANONICAL}/sitemap.xml"
        record("sitemap", sitemap_ok, {"status": status, "final_url": final})
        urls = [node.text or "" for node in ET.fromstring(body).iter() if node.tag.endswith("}loc") or node.tag == "loc"]
    except Exception as error:
        record("sitemap", False, str(error))
        urls = []

    bad_urls = [urllib.parse.urljoin(CANONICAL, path) for path in sorted(BAD_PATHS)]
    record("malformed_paths_absent_from_sitemap", sitemap_ok and not any(urlsplit_path(url) in BAD_PATHS for url in urls), [url for url in urls if urlsplit_path(url) in BAD_PATHS])
    for url in bad_urls:
        try:
            status, final, _ = fetch(url)
            record(f"gone:{url}", status in (404, 410), {"status": status, "final_url": final})
        except Exception as error:
            record(f"gone:{url}", False, str(error))

    def check_sitemap_url(url):
        page_checks = []
        try:
            parsed = urllib.parse.urlsplit(url)
            canonical = (parsed.scheme == "https" and parsed.netloc == "www.yishunagain.com"
                         and (parsed.path in ("", "/") or not parsed.path.endswith("/"))
                         and not parsed.query and not parsed.fragment)
            status, final, body = fetch(url)
            page_ok = canonical and status == 200 and canonical_url(final) == canonical_url(url)
            detail = {"status": status, "final_url": final, "canonical_url": canonical}
            page_checks.append({"check": f"sitemap_url:{url}", "ok": page_ok, "detail": detail})
            if status == 200 and parsed.path not in ("/sitemap.xml", "/robots.txt"):
                parser = Links()
                parser.feed(body.decode("utf-8", errors="replace"))
                html_ok = not parser.noindex and len(parser.canonicals) == 1 and canonical_url(parser.canonicals[0]) == canonical_url(url)
                internal_links_ok = True
                for href in parser.hrefs:
                    target_url = urllib.parse.urlsplit(urllib.parse.urljoin(url, href))
                    if target_url.netloc.lower() in ("www.yishunagain.com", "yishunagain.com"):
                        direct = target_url.scheme == "https" and target_url.netloc.lower() == "www.yishunagain.com"
                        direct &= target_url.path == "/" or not target_url.path.endswith("/")
                        internal_links_ok &= direct and target_url.path not in BAD_PATHS
                page_checks.append({"check": f"html:{url}", "ok": html_ok, "detail": {"canonical": parser.canonicals, "noindex": parser.noindex}})
                page_checks.append({"check": f"internal_links:{url}", "ok": internal_links_ok, "detail": "internal links use canonical host and slash form"})
        except Exception as error:
            page_checks.append({"check": f"sitemap_url:{url}", "ok": False, "detail": str(error)})
        return page_checks

    with ThreadPoolExecutor(max_workers=8) as pool:
        for page_checks in pool.map(check_sitemap_url, urls):
            checks.extend(page_checks)

    variants = ["http://www.yishunagain.com/", "https://yishunagain.com/"]
    variants += [f"{CANONICAL}{path}/" for path in ("/map", "/patterns", "/about")]
    variants += [f"{CANONICAL}{path}" for path in (
        "/incidents/pma-rider-crash-yishun-pedestrian-path",
    )]
    variants += [value.strip() for value in os.environ.get("AUDIT_REDIRECT_URLS", "").split(",") if value.strip()]
    for url in dict.fromkeys(variants):
        try:
            status, final, _ = fetch(url)
            original = urllib.parse.urlsplit(url)
            destination = urllib.parse.urlsplit(final)
            good = status == 301 and destination.netloc == "www.yishunagain.com" and destination.scheme == "https"
            if original.path != "/":
                good &= destination.path != "/"
            if good:
                final_status, final_url, _ = fetch(final)
                good = final_status == 200 and final_url == final
            record(f"redirect:{url}", good, {"status": status, "final_url": final})
        except Exception as error:
            record(f"redirect:{url}", False, str(error))

    report = {"site": CANONICAL, "passed": all(item["ok"] for item in checks), "checks": checks}
    with open("audit_report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


def urlsplit_path(url):
    return urllib.parse.urlsplit(url).path


def canonical_url(url):
    parsed = urllib.parse.urlsplit(url)
    path = "" if parsed.path == "/" else parsed.path
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, parsed.fragment))


if __name__ == "__main__":
    sys.exit(main())

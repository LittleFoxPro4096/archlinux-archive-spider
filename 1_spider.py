#!/bin/env python3
# -*- coding: utf-8 -*-
"""
Arch Linux Archive spider v2.0
Stage 1: generate download manifests

Workflow:
    1. Read mirror URL, snapshot date and repo subdirectories from
       archive.properties;
    2. Fetch the repo directory index and collect the repo metadata files
       (core.db, core.files, etc. - pacman needs them to sync the repo);
    3. Download the <repo>.db.tar.gz archive of each repo for the given date;
    4. Parse the desc file of every package and extract %NAME% / %FILENAME%;
    5. Write manifest/<repo>.txt with metadata links at the head, followed
       by the real package URLs under /packages;
    6. Run 2_fetch.sh afterwards to download everything with aria2c.
"""
import io
import os
import shutil
import sys
import tarfile
from concurrent.futures.thread import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

PKG_SUFFIXES = ("zst", "xz", "gz")  # package compression formats (HTML index fallback)
USER_AGENT = "archlinux-archive-spider/2.0"


# Properties file reader
class Properties:
    def __init__(self, file_name, encoding="UTF-8"):
        self.file_name = file_name
        self.encoding = encoding

    def load_properties(self):
        try:
            file = open(self.file_name, "r", encoding=self.encoding)
            properties = {}
            for line in file.readlines():
                line = line.strip()
                if line and not line.startswith("#") and line.find("=") != -1:
                    attribute = line.split("=", 1)
                    properties[attribute[0].strip()] = attribute[1].strip()
        except Exception as e:
            raise e
        else:
            file.close()
        return properties


# Parse the desc file of a single package inside the db, return a field dict
def parse_desc(text):
    fields = {}
    key = None
    for line in text.splitlines():
        if line.startswith("%") and line.endswith("%"):
            key = line.strip("%")
            fields[key] = []
        elif key and line:
            fields[key].append(line)
    return fields


# Build the real download URL under /packages/<first-letter>/<pkgname>/<filename>
def build_package_url(packages_url, name, filename):
    return packages_url + "/" + name[0] + "/" + name + "/" + filename


# Fetch the repo directory index and split its entries into
# (metadata URLs, package filenames).
# Metadata = plain files that are not packages (e.g. core.db, core.db.tar.gz,
# core.files, core.links.tar.gz); pacman needs them to sync the repo.
# Server-side backup copies (*.old) are skipped.
def fetch_repo_index(session, page_url):
    resp = session.get(page_url, timeout=120)
    resp.raise_for_status()
    resp.encoding = "UTF-8"
    metadata_urls = []
    package_names = []
    for a in BeautifulSoup(resp.text, features="html.parser").find_all("a"):
        href = a.get("href") or ""
        # skip parent dir, subdirs, query links and absolute URLs
        if not href or href.endswith("/") or href.startswith("?") or "://" in href:
            continue
        if ".pkg.tar." in href:
            package_names.append(href)
        elif not href.endswith(".old"):
            metadata_urls.append(page_url + href)
    return metadata_urls, package_names


# Thread task: process one repo and write manifest/<repo>.txt
# (metadata links at the head, package links afterwards)
def thread_handler(manifest_dir, archive_url, packages_url, archive_date,
                   repo_name, repo_url, with_sig=False):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    page_url = archive_url + "/" + archive_date + "/" + repo_url + "/"
    print("[" + repo_name + "] fetching repo index: " + page_url)
    metadata_urls, package_names = fetch_repo_index(session, page_url)
    print("[" + repo_name + "] found " + str(len(metadata_urls)) + " metadata files")

    db_url = page_url + repo_name + ".db.tar.gz"
    resp = session.get(db_url, timeout=120)

    package_urls = []
    if resp.status_code == 404:
        # ---- Fallback: old dates before the layout change, where the repos
        # ---- directory still stores package files directly
        print("[" + repo_name + "] db archive not found, "
              "using packages from the HTML index (old layout)")
        for filename in package_names:
            package_urls.append(page_url + filename)
            if with_sig:
                package_urls.append(page_url + filename + ".sig")
    else:
        resp.raise_for_status()
        # ---- New layout: parse desc files inside <repo>.db.tar.gz
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tf:
            for member in tf.getmembers():
                if not member.name.endswith("/desc"):
                    continue
                desc = parse_desc(tf.extractfile(member).read().decode("utf-8", "replace"))
                name = desc.get("NAME", [None])[0]
                filename = desc.get("FILENAME", [None])[0]
                if name and filename:
                    package_urls.append(build_package_url(packages_url, name, filename))
                    if with_sig:
                        package_urls.append(build_package_url(packages_url, name, filename) + ".sig")

    url_list = metadata_urls + package_urls
    with open(manifest_dir + "/" + repo_name + ".txt", "w", encoding="UTF-8") as file:
        for url in url_list:
            file.write(url + "\n")
    print("[" + repo_name + "] manifest written to manifest/" + repo_name + ".txt"
          " (" + str(len(metadata_urls)) + " metadata + "
          + str(len(package_urls)) + " packages)")


# Main function
def main():
    manifest_dir = "manifest"
    archive_properties = Properties("archive.properties").load_properties()

    archive_url = archive_properties["archive_url"].rstrip("/")
    archive_date = archive_properties["archive_date"]
    # If packages_url is not configured, derive it from archive_url
    # (.../repos -> .../packages)
    packages_url = archive_properties.get(
        "packages_url", archive_url.rsplit("/repos", 1)[0] + "/packages").rstrip("/")
    with_sig = archive_properties.get("with_sig", "false").lower() == "true"

    # Repo list: every <repo>_url key in the properties file
    # (excluding archive_url / packages_url)
    repo_list = [key[:-4] for key in archive_properties
                 if key.endswith("_url") and key not in ("archive_url", "packages_url")]
    if not repo_list:
        print("error: no repo configured in archive.properties"
              " (e.g. core_url=core/os/x86_64)", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(manifest_dir):
        shutil.rmtree(manifest_dir, ignore_errors=True)
    os.mkdir(manifest_dir)

    thread_pool = ThreadPoolExecutor(max_workers=4)
    for repo_name in repo_list:
        thread_pool.submit(thread_handler,
                           manifest_dir,
                           archive_url,
                           packages_url,
                           archive_date,
                           repo_name,
                           archive_properties[repo_name + "_url"],
                           with_sig)
    thread_pool.shutdown(wait=True)
    print("Done. Run 2_fetch.sh to start downloading.")


if __name__ == "__main__":
    main()

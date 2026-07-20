#!/usr/bin/env python3
"""Generate a static Kissena forum archive from an exported SQLite database."""

import argparse
import bisect
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections import defaultdict
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit

from vf_templates import AVATAR_PLACEHOLDER_SVG, create_environment, render_page


PAGE_SIZE = 30
SOURCE_HOST = "kissenacycling.vanillacommunity.com"
OUTPUT_MARKER = ".vf-static-output"
PREVIEW_MARKER = "THIS_IS_A_PREVIEW"


def row_dict(row):
    return dict(row) if row is not None else None


def accepted_image(content_type):
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower().startswith("image/")


def safe_export_path(data_dir, relative_path):
    if not relative_path:
        return None
    path = (data_dir / relative_path).resolve()
    try:
        path.relative_to(data_dir.resolve())
    except ValueError:
        return None
    return path


class LinkCollector(HTMLParser):
    """Collect links, sources, and IDs from generated HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag in ("a", "link") and values.get("href"):
            self.references.append(("href", values["href"]))
        if tag in ("img", "script", "iframe", "source") and values.get("src"):
            self.references.append(("src", values["src"]))


class StaticSiteGenerator:
    def __init__(
        self, db_path, output_path, base_url, preview=False, discussion_ids=None
    ):
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme not in ("http", "https")
            or not parsed_base_url.netloc
            or parsed_base_url.path not in ("", "/")
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError(
                "--base-url must be an HTTP(S) origin without a path, query, or fragment"
            )
        self.db_path = db_path.resolve()
        self.data_dir = self.db_path.parent
        self.output_path = output_path.resolve()
        self.base_url = base_url.rstrip("/")
        self.base_netloc = parsed_base_url.netloc.lower()
        self.preview = preview
        self.selected_ids = set(discussion_ids or [])
        self.staging_path = None
        self.connection = None
        self.categories = []
        self.discussions = {}
        self.category_discussions = defaultdict(list)
        self.users = {}
        self.users_by_name = {}
        self.comments_by_discussion = defaultdict(list)
        self.comment_targets = {}
        self.comment_dates = defaultdict(list)
        self.image_urls = {}
        self.image_paths = {}
        self.assumed_image_paths = set()
        self.avatar_paths = {}
        self.export_timestamp = None
        self.page_count = 0

    def connect(self):
        if not self.db_path.is_file():
            raise ValueError(f"database does not exist: {self.db_path}")
        uri = f"{self.db_path.as_uri()}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row

    def query(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchall()

    def query_one(self, sql, parameters=()):
        return self.connection.execute(sql, parameters).fetchone()

    def load_data(self):
        self.categories = [
            row_dict(row)
            for row in self.query(
                "SELECT * FROM categories ORDER BY parent_category_id NULLS FIRST, name"
            )
        ]
        all_discussions = {
            row["discussion_id"]: row_dict(row)
            for row in self.query("SELECT * FROM discussions")
        }

        if self.preview:
            if not self.selected_ids:
                raise ValueError("--preview requires at least one --discussion ID")
            missing = sorted(self.selected_ids - set(all_discussions))
            if missing:
                raise ValueError(
                    "unknown discussion ID(s): " + ", ".join(map(str, missing))
                )
            self.discussions = {
                discussion_id: all_discussions[discussion_id]
                for discussion_id in sorted(self.selected_ids)
            }
        elif self.selected_ids:
            raise ValueError("--discussion is valid only with --preview")
        else:
            self.discussions = all_discussions

        self.users = {
            row["user_id"]: row_dict(row)
            for row in self.query(
                """
                SELECT user_id, name, title, label, date_inserted,
                       count_discussions, count_comments
                FROM users
                """
            )
        }
        self.users_by_name = {
            user["name"].casefold(): user["user_id"] for user in self.users.values()
        }

        metadata = self.query(
            """
            SELECT comment_id, discussion_id, date_inserted, insert_user_id
            FROM comments
            ORDER BY discussion_id, date_inserted ASC, comment_id ASC
            """
        )
        for row in metadata:
            discussion_id = row["discussion_id"]
            entries = self.comments_by_discussion[discussion_id]
            comment_position = len(entries)
            entry = {
                "comment_id": row["comment_id"],
                "date_inserted": row["date_inserted"],
                "insert_user_id": row["insert_user_id"],
            }
            entries.append(entry)
            page = comment_position // PAGE_SIZE + 1
            self.comment_targets[row["comment_id"]] = (discussion_id, page)

        for discussion_id, entries in self.comments_by_discussion.items():
            self.comment_dates[discussion_id] = [
                ((entry["date_inserted"] or ""), entry["comment_id"])
                for entry in entries
            ]

        for discussion in self.discussions.values():
            entries = self.comments_by_discussion.get(discussion["discussion_id"], [])
            last_comment = entries[-1] if entries else None
            author = self.users.get(discussion["insert_user_id"])
            last_user_id = (
                last_comment["insert_user_id"]
                if last_comment
                else discussion["insert_user_id"]
            )
            last_user = self.users.get(last_user_id)
            listing = dict(discussion)
            listing.update(
                author_name=author["name"] if author else None,
                avatar_user_id=last_user_id,
                profile_user_id=(
                    last_user_id
                    if last_user
                    else (author["user_id"] if author else None)
                ),
                last_reply_name=(
                    last_user["name"]
                    if last_user
                    else (author["name"] if author else None)
                ),
                last_activity_date=(
                    last_comment["date_inserted"]
                    if last_comment
                    else discussion["date_inserted"]
                ),
            )
            self.category_discussions[discussion["category_id"]].append(listing)

        for listings in self.category_discussions.values():
            listings.sort(
                key=lambda item: (
                    item["pinned"] or 0,
                    item["last_activity_date"] or "",
                ),
                reverse=True,
            )

        for row in self.query(
            "SELECT image_id, original_url, content_type, file_path FROM images"
        ):
            source = safe_export_path(self.data_dir, row["file_path"])
            if source and accepted_image(row["content_type"]):
                public_path = "/" + PurePosixPath(row["file_path"]).as_posix()
                self.image_urls[row["original_url"]] = public_path
                self.image_paths[row["image_id"]] = public_path
                self.assumed_image_paths.add(public_path)

        for row in self.query(
            "SELECT user_id, content_type, file_path FROM user_avatars"
        ):
            source = safe_export_path(self.data_dir, row["file_path"])
            if source and source.is_file() and accepted_image(row["content_type"]):
                self.avatar_paths[row["user_id"]] = (
                    "/" + PurePosixPath(row["file_path"]).as_posix()
                )

        export_row = self.query_one(
            "SELECT value FROM export_meta WHERE key = 'export_timestamp'"
        )
        self.export_timestamp = export_row["value"] if export_row else None

    def site_url(self, path):
        return f"{self.base_url}{path}"

    def href_index(self):
        return self.site_url("/index.html")

    def href_category(self, category_id):
        return self.site_url(f"/category/{int(category_id)}.html")

    def href_discussion(self, discussion_id, page=1, anchor=None):
        discussion_id = int(discussion_id)
        page = max(1, int(page))
        if page == 1:
            path = f"/discussion/{discussion_id}.html"
        else:
            path = f"/discussion/{discussion_id}-p{page}.html"
        url = self.site_url(path)
        return f"{url}#{anchor}" if anchor else url

    def href_comment(self, comment_id):
        target = self.comment_targets.get(int(comment_id))
        if not target:
            return None
        discussion_id, page = target
        return self.href_discussion(
            discussion_id, page, anchor=f"Comment_{int(comment_id)}"
        )

    def href_profile(self, user_id, username=None):
        return self.site_url(f"/profile/{int(user_id)}.html")

    def href_avatar(self, user_id):
        path = self.avatar_paths.get(int(user_id), "/avatar-placeholder.svg")
        return self.site_url(path)

    def href_image(self, image_id):
        path = self.image_paths.get(int(image_id))
        return self.site_url(path) if path else ""

    def latest_comment(self, discussion_id, reference_date):
        entries = self.comments_by_discussion.get(discussion_id, [])
        if not entries:
            return None
        if reference_date is None:
            return entries[-1]
        dates = self.comment_dates[discussion_id]
        index = bisect.bisect_right(dates, (reference_date, float("inf"))) - 1
        return entries[index] if index >= 0 else None

    def resolve_forum_link(self, url, reference_date=None):
        parsed = urlsplit(unescape(url))
        if parsed.netloc and parsed.netloc.lower() != SOURCE_HOST:
            return None
        if not parsed.netloc and not parsed.path.startswith("/"):
            return None
        path_parts = [part for part in parsed.path.split("/") if part]

        if path_parts[:2] == ["home", "leaving"]:
            target = parse_qs(parsed.query).get("target", [None])[0]
            if target and urlsplit(target).scheme in ("http", "https"):
                return target
            return None

        if len(path_parts) >= 3 and path_parts[:2] == ["discussion", "comment"]:
            if path_parts[2].isdigit():
                return self.href_comment(int(path_parts[2]))
            return None

        if len(path_parts) >= 2 and path_parts[0] == "profile":
            identity = unquote(path_parts[1])
            if identity.isdigit() and int(identity) in self.users:
                return self.href_profile(int(identity))
            user_id = self.users_by_name.get(identity.casefold())
            if user_id is not None:
                return self.href_profile(user_id)
            if parsed.netloc:
                return url
            return f"https://{SOURCE_HOST}{parsed.geturl()}"

        if (
            len(path_parts) >= 2
            and path_parts[0] == "discussion"
            and path_parts[1].isdigit()
        ):
            discussion_id = int(path_parts[1])
            if discussion_id not in self.discussions and not self.preview:
                return f"https://{SOURCE_HOST}{parsed.geturl()}"
            comment_fragment = re.fullmatch(
                r"Comment_(\d+)", parsed.fragment, re.IGNORECASE
            )
            if comment_fragment:
                return self.href_comment(int(comment_fragment.group(1)))
            if parsed.fragment.lower() == "latest":
                comment = self.latest_comment(discussion_id, reference_date)
                if not comment:
                    return self.href_discussion(discussion_id)
                return self.href_comment(comment["comment_id"])
            return self.href_discussion(
                discussion_id, anchor=parsed.fragment or None
            )

        if parsed.path.startswith("/"):
            return f"https://{SOURCE_HOST}{parsed.geturl()}"
        return None

    def rewrite_forum_links(self, html, reference_date=None):
        if not html:
            return ""

        def replace_anchor(match):
            original_url = match.group(3)
            local = self.resolve_forum_link(original_url, reference_date)
            if not local:
                return match.group(0)
            body = match.group(5)
            if "<" not in body and unescape(body.strip()) == unescape(original_url):
                leading_space = body[: len(body) - len(body.lstrip())]
                trailing_space = body[len(body.rstrip()) :]
                body = f"{leading_space}{escape(local)}{trailing_space}"
            return (
                f"{match.group(1)}{match.group(2)}{escape(local, quote=True)}"
                f"{match.group(2)}{match.group(4)}{body}</a>"
            )

        return re.sub(
            r'(<a\b[^>]*?href\s*=\s*)(["\x27])(.+?)\2([^>]*>)(.*?)</a>',
            replace_anchor,
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

    def rewrite_images(self, html):
        if not html:
            return ""
        html = re.sub(r'\s+srcset="[^"]*"', "", html)

        def replace_source(match):
            original = match.group(1)
            local = self.image_urls.get(original)
            if local:
                return f'src="{self.site_url(local)}"'
            if original.startswith("/"):
                return f'src="https://{SOURCE_HOST}{original}"'
            return match.group(0)

        return re.sub(r'src="([^"]+)"', replace_source, html)

    def rewrite_content(self, html, reference_date=None):
        return self.rewrite_forum_links(self.rewrite_images(html), reference_date)

    def create_environment(self):
        return create_environment(
            self.rewrite_content,
            href_index=self.href_index,
            href_category=self.href_category,
            href_discussion=self.href_discussion,
            href_comment=self.href_comment,
            href_profile=self.href_profile,
            href_avatar=self.href_avatar,
            href_image=self.href_image,
        )

    def write_page(self, relative_path, html):
        destination = self.staging_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8")
        self.page_count += 1

    def load_comments(self, discussion_id):
        return [
            row_dict(row)
            for row in self.query(
                """
                SELECT c.*, u.name AS user_name, u.title AS user_title, u.user_id
                FROM comments c LEFT JOIN users u ON c.insert_user_id = u.user_id
                WHERE c.discussion_id = ?
                ORDER BY c.date_inserted ASC, c.comment_id ASC
                """,
                [discussion_id],
            )
        ]

    def render_html(self):
        environment = self.create_environment()
        common = {"export_timestamp": self.export_timestamp}
        self.write_page(
            "index.html",
            render_page(
                environment,
                "index.html",
                "Forum Archive",
                categories=self.categories,
                **common,
            ),
        )

        for category in self.categories:
            self.write_page(
                Path("category") / f'{category["category_id"]}.html',
                render_page(
                    environment,
                    "category.html",
                    f'{category["name"]} - Forum Archive',
                    category=category,
                    discussions=self.category_discussions.get(
                        category["category_id"], []
                    ),
                    **common,
                ),
            )

        referenced_users = set()
        for discussion_id in sorted(self.discussions):
            discussion = self.discussions[discussion_id]
            category = next(
                item
                for item in self.categories
                if item["category_id"] == discussion["category_id"]
            )
            author = self.users.get(discussion["insert_user_id"])
            if author:
                referenced_users.add(author["user_id"])
            comments = self.load_comments(discussion_id)
            referenced_users.update(
                comment["user_id"] for comment in comments if comment["user_id"]
            )
            total_pages = max(1, (len(comments) + PAGE_SIZE - 1) // PAGE_SIZE)
            for page in range(1, total_pages + 1):
                page_comments = comments[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
                filename = (
                    f"{discussion_id}.html"
                    if page == 1
                    else f"{discussion_id}-p{page}.html"
                )
                self.write_page(
                    Path("discussion") / filename,
                    render_page(
                        environment,
                        "discussion.html",
                        f'{discussion["name"]} - Forum Archive',
                        discussion=discussion,
                        category=category,
                        author=author,
                        comments=page_comments,
                        page=page,
                        per_page=PAGE_SIZE,
                        total_pages=total_pages,
                        total_comments=len(comments),
                        allowed_per_page=None,
                        **common,
                    ),
                )

        profile_ids = referenced_users if self.preview else set(self.users)
        for user_id in sorted(profile_ids):
            user = self.users.get(user_id)
            if not user:
                continue
            self.write_page(
                Path("profile") / f"{user_id}.html",
                render_page(
                    environment,
                    "profile.html",
                    f'{user["name"]} - Forum Archive',
                    user=user,
                    **common,
                ),
            )

    def stage_assets(self):
        for directory_name in ("images", "avatars"):
            source = self.data_dir / directory_name
            destination = self.staging_path / directory_name
            if source.is_dir():
                destination.symlink_to(source.resolve(), target_is_directory=True)
            else:
                destination.mkdir()
        (self.staging_path / "avatar-placeholder.svg").write_text(
            AVATAR_PLACEHOLDER_SVG, encoding="utf-8"
        )

    def resolve_local_reference(self, source_file, url):
        parsed = urlsplit(url)
        if parsed.netloc:
            if (
                parsed.scheme not in ("", "http", "https")
                or parsed.netloc.lower() != self.base_netloc
            ):
                return None
        elif parsed.scheme:
            return None
        if parsed.path in ("", "/"):
            target = self.staging_path / "index.html"
        elif parsed.path.startswith("/"):
            target = self.staging_path / unquote(parsed.path.lstrip("/"))
        else:
            target = source_file.parent / unquote(parsed.path)
        # Normalize `..` segments without resolving asset-directory symlinks.
        # The lexical path must remain inside staging, while `exists()` below
        # is allowed to follow the images/ and avatars/ links to the export.
        target = Path(os.path.abspath(target))
        try:
            target.relative_to(self.staging_path.absolute())
        except ValueError:
            raise ValueError(f"path escapes output: {url} in {source_file}")
        return target, parsed.fragment

    def validate(self):
        if self.preview:
            return
        errors = []
        parsed_files = {}
        html_files = sorted(self.staging_path.rglob("*.html"))
        for html_file in html_files:
            parser = LinkCollector()
            parser.feed(html_file.read_text(encoding="utf-8"))
            parsed_files[html_file.resolve()] = parser

        for html_file in html_files:
            parser = parsed_files[html_file.resolve()]
            for attribute, url in parser.references:
                try:
                    resolved = self.resolve_local_reference(html_file, url)
                except ValueError as error:
                    errors.append(str(error))
                    continue
                if resolved is None:
                    continue
                target, fragment = resolved
                if not target.exists():
                    if (
                        attribute == "src"
                        and unquote(urlsplit(url).path) in self.assumed_image_paths
                    ):
                        continue
                    errors.append(f"missing {attribute} target {url} in {html_file}")
                    continue
                if fragment and target.suffix == ".html":
                    target_parser = parsed_files.get(target)
                    if target_parser is None:
                        target_parser = LinkCollector()
                        target_parser.feed(target.read_text(encoding="utf-8"))
                        parsed_files[target] = target_parser
                    if fragment not in target_parser.ids:
                        errors.append(
                            f"missing fragment #{fragment} in {target} (from {html_file})"
                        )
                if len(errors) >= 50:
                    break
            if len(errors) >= 50:
                break
        if errors:
            details = "\n".join(f"  - {error}" for error in errors)
            raise ValueError(f"generated link validation failed:\n{details}")

    def prepare_staging(self):
        output_parent = self.output_path.parent
        output_parent.mkdir(parents=True, exist_ok=True)
        if self.output_path == self.data_dir or self.output_path == Path("/"):
            raise ValueError(f"unsafe output directory: {self.output_path}")
        if self.output_path.exists() and not (
            (self.output_path / OUTPUT_MARKER).exists()
            or (self.output_path / PREVIEW_MARKER).exists()
        ):
            raise ValueError(
                f"refusing to replace unrecognized directory: {self.output_path}"
            )
        self.staging_path = Path(
            tempfile.mkdtemp(prefix=f".{self.output_path.name}-", dir=output_parent)
        )
        (self.staging_path / OUTPUT_MARKER).write_text(
            "Generated by vf_static.py\n", encoding="utf-8"
        )
        if self.preview:
            (self.staging_path / PREVIEW_MARKER).write_text(
                "Incomplete local preview; do not deploy.\n", encoding="utf-8"
            )

    def publish_staging(self):
        if self.output_path.exists():
            if self.output_path.is_symlink() or self.output_path.is_file():
                self.output_path.unlink()
            else:
                shutil.rmtree(self.output_path)
        os.replace(str(self.staging_path), str(self.output_path))
        self.staging_path = None

    def build(self):
        started = time.monotonic()
        try:
            self.connect()
            self.load_data()
            self.prepare_staging()
            render_started = time.monotonic()
            self.render_html()
            render_elapsed = time.monotonic() - render_started
            assets_started = time.monotonic()
            self.stage_assets()
            assets_elapsed = time.monotonic() - assets_started
            validation_started = time.monotonic()
            self.validate()
            validation_elapsed = time.monotonic() - validation_started
            self.publish_staging()
        finally:
            if self.connection is not None:
                self.connection.close()
            if self.staging_path and self.staging_path.exists():
                shutil.rmtree(self.staging_path)

        total_elapsed = time.monotonic() - started
        mode = "preview" if self.preview else "full"
        print(f"Built {mode} site at {self.output_path}")
        print(f"HTML pages: {self.page_count}")
        print(f"HTML rendering: {render_elapsed:.1f}s")
        print(f"Asset staging: {assets_elapsed:.1f}s")
        print(f"Validation: {validation_elapsed:.1f}s")
        print(f"Total: {total_elapsed:.1f}s")
        if self.preview:
            print("WARNING: this is an incomplete preview and must not be deployed")


def parse_args():
    parser = argparse.ArgumentParser(description="Build the static forum archive")
    parser.add_argument(
        "--db", type=Path, default=Path("vanilla.db"), help="SQLite export"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("_site"), help="output directory"
    )
    parser.add_argument(
        "--base-url",
        default="https://d1b51nugwabl8z.cloudfront.net",
        help="absolute origin used for generated internal links",
    )
    parser.add_argument(
        "--preview", action="store_true", help="build selected discussions only"
    )
    parser.add_argument(
        "--discussion",
        type=int,
        action="append",
        default=[],
        metavar="ID",
        help="discussion to include in preview; repeatable",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        generator = StaticSiteGenerator(
            args.db,
            args.output,
            args.base_url,
            preview=args.preview,
            discussion_ids=args.discussion,
        )
        generator.build()
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SystemExit(f"error: {error}")


if __name__ == "__main__":
    main()

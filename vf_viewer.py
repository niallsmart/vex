#!/usr/bin/env python3
"""Browse an exported Vanilla forum with Flask."""

import argparse
import re
import sqlite3
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from flask import Flask, Response, abort, g, redirect, request, send_file, url_for

from vf_templates import (
    AVATAR_PLACEHOLDER_SVG,
    create_environment,
    format_date,
    render_page,
)


PAGE_SIZE = 30
ALLOWED_PAGE_SIZES = (10, 20, 30, 50, 100)

app = Flask(__name__)
app.config["DATABASE"] = "test.db"
app.config["DATA_DIR"] = Path(".")
app.config["PUBLIC_URL"] = None


def get_db():
    """Get the database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Close the request's database connection."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    """Execute a query and return rows."""
    rows = get_db().execute(query, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def href_index():
    return url_for("index")


def href_category(category_id):
    return url_for("category", category_id=category_id)


def href_discussion(discussion_id, page=1, anchor=None):
    values = {"discussion_id": discussion_id}
    if page != 1 or request.endpoint == "discussion":
        per_page = request.args.get("per_page", PAGE_SIZE, type=int)
        if per_page not in ALLOWED_PAGE_SIZES:
            per_page = PAGE_SIZE
        values.update(
            page=page,
            per_page=per_page,
        )
    if anchor:
        values["_anchor"] = anchor
    return url_for("discussion", **values)


def href_comment(comment_id):
    return url_for("comment_permalink", comment_id=comment_id)


def href_profile(user_id, username=None):
    values = {"user_id": user_id}
    if username:
        values["username"] = username
    return url_for("profile", **values)


def href_avatar(user_id):
    return url_for("avatar", user_id=user_id)


def href_image(image_id):
    return url_for("image", image_id=image_id)


def rewrite_images(html):
    """Rewrite exported image URLs to the viewer's local image route."""
    if not html:
        return ""
    html = re.sub(r'\s+srcset="[^"]*"', "", html)

    def replace_img(match):
        image = query_db(
            "SELECT image_id FROM images WHERE original_url = ?",
            [match.group(1)],
            one=True,
        )
        if image:
            return f'src="{href_image(image["image_id"])}"'
        return match.group(0)

    return re.sub(r'src="([^"]+)"', replace_img, html)


def archive_url(path):
    """Build an absolute archive URL from configuration or the request host."""
    base_url = app.config.get("PUBLIC_URL") or request.url_root
    return f'{base_url.rstrip("/")}{path}'


def resolve_forum_link(url, reference_date=None):
    """Map links from the retired Vanilla host into the local viewer."""
    parsed = urlsplit(unescape(url))
    if parsed.netloc and parsed.netloc.lower() != "kissenacycling.vanillacommunity.com":
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
            return archive_url(f"/discussion/comment/{path_parts[2]}")
        return None

    if len(path_parts) >= 2 and path_parts[0] == "profile":
        local = f"/profile/{path_parts[1]}"
        if len(path_parts) >= 3:
            local = f"{local}/{path_parts[2]}"
        return archive_url(local)

    if len(path_parts) >= 2 and path_parts[0] == "discussion" and path_parts[1].isdigit():
        discussion_id = int(path_parts[1])
        comment_fragment = re.fullmatch(r"Comment_(\d+)", parsed.fragment, re.IGNORECASE)
        if comment_fragment:
            return archive_url(f"/discussion/comment/{comment_fragment.group(1)}")
        if parsed.fragment.lower() == "latest":
            latest_comment = query_db(
                """
                SELECT comment_id, date_inserted
                FROM comments
                WHERE discussion_id = ? AND (? IS NULL OR date_inserted <= ?)
                ORDER BY date_inserted DESC, comment_id DESC
                LIMIT 1
                """,
                [discussion_id, reference_date, reference_date],
                one=True,
            )
            if not latest_comment:
                return archive_url(f"/discussion/{discussion_id}")
            position = query_db(
                """
                SELECT COUNT(*) AS cnt
                FROM comments
                WHERE discussion_id = ?
                  AND (date_inserted < ? OR (date_inserted = ? AND comment_id <= ?))
                """,
                [
                    discussion_id,
                    latest_comment["date_inserted"],
                    latest_comment["date_inserted"],
                    latest_comment["comment_id"],
                ],
                one=True,
            )["cnt"]
            page = max(1, (position + PAGE_SIZE - 1) // PAGE_SIZE)
            local = archive_url(
                f"/discussion/{discussion_id}?page={page}&per_page={PAGE_SIZE}"
            )
            return f'{local}#Comment_{latest_comment["comment_id"]}'
        local = archive_url(f"/discussion/{discussion_id}")
        return f"{local}#{parsed.fragment}" if parsed.fragment else local

    return None


def rewrite_forum_links(html, reference_date=None):
    """Rewrite links embedded in exported post HTML."""
    if not html:
        return ""

    def replace_anchor(match):
        original_url = match.group(3)
        local = resolve_forum_link(original_url, reference_date)
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


def rewrite_content(html, reference_date=None):
    return rewrite_forum_links(rewrite_images(html), reference_date)


template_environment = create_environment(
    rewrite_content,
    href_index=href_index,
    href_category=href_category,
    href_discussion=href_discussion,
    href_comment=href_comment,
    href_profile=href_profile,
    href_avatar=href_avatar,
    href_image=href_image,
)


def render(template_name, title, **context):
    return render_page(template_environment, template_name, title, **context)


@app.route("/")
def index():
    categories = query_db(
        "SELECT * FROM categories ORDER BY parent_category_id NULLS FIRST, name"
    )
    return render("index.html", "Forum Archive", categories=categories)


@app.route("/category/<int:category_id>")
def category(category_id):
    category_row = query_db(
        "SELECT * FROM categories WHERE category_id = ?", [category_id], one=True
    )
    if not category_row:
        abort(404)

    discussions = query_db(
        """
        SELECT d.*, u.name AS author_name,
               COALESCE(last_comment.last_user_id, d.insert_user_id) AS avatar_user_id,
               CASE
                   WHEN last_commenter.user_id IS NOT NULL THEN last_commenter.user_id
                   WHEN u.user_id IS NOT NULL THEN u.user_id
               END AS profile_user_id,
               COALESCE(last_commenter.name, u.name) AS last_reply_name,
               COALESCE(last_comment.last_date, d.date_inserted) AS last_activity_date
        FROM discussions d
        LEFT JOIN users u ON d.insert_user_id = u.user_id
        LEFT JOIN (
            SELECT discussion_id, MAX(date_inserted) AS last_date,
                   (SELECT insert_user_id FROM comments c2
                    WHERE c2.discussion_id = c.discussion_id
                    ORDER BY date_inserted DESC LIMIT 1) AS last_user_id
            FROM comments c GROUP BY discussion_id
        ) last_comment ON d.discussion_id = last_comment.discussion_id
        LEFT JOIN users last_commenter ON last_comment.last_user_id = last_commenter.user_id
        WHERE d.category_id = ?
        ORDER BY d.pinned DESC, last_activity_date DESC
        """,
        [category_id],
    )
    return render(
        "category.html",
        f'{category_row["name"]} - Forum Archive',
        category=category_row,
        discussions=discussions,
    )


@app.route("/profile/<user_id>")
@app.route("/profile/<user_id>/<username>")
def profile(user_id, username=None):
    if user_id.isdigit():
        user = query_db(
            """
            SELECT user_id, name, title, label, date_inserted,
                   count_discussions, count_comments
            FROM users WHERE user_id = ?
            """,
            [int(user_id)],
            one=True,
        )
    else:
        user = query_db(
            """
            SELECT user_id, name, title, label, date_inserted,
                   count_discussions, count_comments
            FROM users WHERE name = ? COLLATE NOCASE
            """,
            [unquote(user_id)],
            one=True,
        )
    if not user:
        abort(404)
    return render("profile.html", f'{user["name"]} - Forum Archive', user=user)


@app.route("/discussion/<int:discussion_id>")
def discussion(discussion_id):
    discussion_row = query_db(
        "SELECT * FROM discussions WHERE discussion_id = ?", [discussion_id], one=True
    )
    if not discussion_row:
        abort(404)
    category_row = query_db(
        "SELECT * FROM categories WHERE category_id = ?",
        [discussion_row["category_id"]],
        one=True,
    )
    author = query_db(
        "SELECT * FROM users WHERE user_id = ?",
        [discussion_row["insert_user_id"]],
        one=True,
    )

    allowed_per_page = list(ALLOWED_PAGE_SIZES)
    per_page = request.args.get("per_page", PAGE_SIZE, type=int)
    if per_page not in allowed_per_page:
        per_page = PAGE_SIZE
    page = max(1, request.args.get("page", 1, type=int))
    total_comments = query_db(
        "SELECT COUNT(*) AS cnt FROM comments WHERE discussion_id = ?",
        [discussion_id],
        one=True,
    )["cnt"]
    total_pages = max(1, (total_comments + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    comments = query_db(
        f"""
        SELECT c.*, u.name AS user_name, u.title AS user_title, u.user_id
        FROM comments c LEFT JOIN users u ON c.insert_user_id = u.user_id
        WHERE c.discussion_id = ?
        ORDER BY c.date_inserted ASC, c.comment_id ASC
        LIMIT {per_page} OFFSET {offset}
        """,
        [discussion_id],
    )
    return render(
        "discussion.html",
        f'{discussion_row["name"]} - Forum Archive',
        discussion=discussion_row,
        category=category_row,
        author=author,
        comments=comments,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_comments=total_comments,
        allowed_per_page=allowed_per_page,
    )


@app.route("/discussion/comment/<int:comment_id>", strict_slashes=False)
def comment_permalink(comment_id):
    comment = query_db(
        "SELECT discussion_id, date_inserted FROM comments WHERE comment_id = ?",
        [comment_id],
        one=True,
    )
    if not comment:
        abort(404)
    preceding_comments = query_db(
        """
        SELECT COUNT(*) AS cnt FROM comments
        WHERE discussion_id = ?
          AND (date_inserted < ? OR (date_inserted = ? AND comment_id < ?))
        """,
        [
            comment["discussion_id"],
            comment["date_inserted"],
            comment["date_inserted"],
            comment_id,
        ],
        one=True,
    )["cnt"]
    page = preceding_comments // PAGE_SIZE + 1
    return redirect(
        url_for(
            "discussion",
            discussion_id=comment["discussion_id"],
            page=page,
            per_page=PAGE_SIZE,
            _anchor=f"Comment_{comment_id}",
        )
    )


@app.route("/image/<int:image_id>")
def image(image_id):
    image_row = query_db(
        "SELECT content_type, file_path FROM images WHERE image_id = ?",
        [image_id],
        one=True,
    )
    if not image_row or not image_row["file_path"]:
        abort(404)
    file_path = app.config["DATA_DIR"] / image_row["file_path"]
    if not file_path.exists():
        abort(404)
    return send_file(file_path, mimetype=image_row["content_type"] or "image/jpeg")


@app.route("/avatar/<int:user_id>")
def avatar(user_id):
    avatar_row = query_db(
        "SELECT content_type, file_path FROM user_avatars WHERE user_id = ?",
        [user_id],
        one=True,
    )
    if avatar_row and avatar_row["file_path"]:
        file_path = app.config["DATA_DIR"] / avatar_row["file_path"]
        if file_path.exists():
            return send_file(
                file_path, mimetype=avatar_row["content_type"] or "image/jpeg"
            )
    return Response(AVATAR_PLACEHOLDER_SVG, mimetype="image/svg+xml")


def main():
    parser = argparse.ArgumentParser(description="View exported Vanilla Forums data")
    parser.add_argument("--db", default="test.db", help="SQLite database path")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--public-url", help="Base URL used for rewritten links")
    args = parser.parse_args()

    db_path = Path(args.db)
    app.config["DATABASE"] = args.db
    app.config["DATA_DIR"] = db_path.parent if db_path.parent != Path() else Path(".")
    app.config["PUBLIC_URL"] = args.public_url
    print(f"Starting forum viewer at http://{args.host}:{args.port}")
    print(f"Database: {args.db}")
    print(f"Data directory: {app.config['DATA_DIR']}")
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == "__main__":
    main()

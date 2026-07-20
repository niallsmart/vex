#!/usr/bin/env python3
"""
Vanilla Forums Export Viewer

A web-based interface to browse exported Vanilla Forums data.
Images are served from the filesystem (images/ and avatars/ directories
alongside the database file).

Usage:
    python vf_viewer.py [--db forum.db] [--port 5000]
"""

import argparse
import re
import sqlite3
from datetime import datetime
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from flask import Flask, g, render_template_string, abort, redirect, Response, request, send_file, url_for

app = Flask(__name__)
app.config['DATABASE'] = 'test.db'
app.config['DATA_DIR'] = Path('.')  # Directory containing images/ and avatars/
app.config['PUBLIC_URL'] = None


# =============================================================================
# Database
# =============================================================================

def get_db():
    """Get database connection for current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Close database connection at end of request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query_db(query, args=(), one=False):
    """Execute a query and return results."""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv


# =============================================================================
# Template Helpers
# =============================================================================

def format_date(date_str):
    """Format ISO date string for display."""
    if not date_str:
        return ''
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%b %d, %Y at %I:%M %p')
    except:
        return date_str


def rewrite_images(html):
    """Rewrite image URLs to use local blob storage."""
    if not html:
        return ''

    # Remove srcset attributes (they contain CDN URLs for responsive images)
    html = re.sub(r'\s+srcset="[^"]*"', '', html)

    def replace_img(match):
        url = match.group(1)
        img = query_db(
            "SELECT image_id FROM images WHERE original_url = ?",
            [url], one=True
        )
        if img:
            return f'src="/image/{img["image_id"]}"'
        return match.group(0)

    return re.sub(r'src="([^"]+)"', replace_img, html)


def resolve_forum_link(url, reference_date=None):
    """Map links from the retired Vanilla host into the local viewer."""
    parsed = urlsplit(unescape(url))
    if parsed.netloc and parsed.netloc.lower() != 'kissenacycling.vanillacommunity.com':
        return None
    if not parsed.netloc and not parsed.path.startswith('/'):
        return None

    path_parts = [part for part in parsed.path.split('/') if part]

    if path_parts[:2] == ['home', 'leaving']:
        target = parse_qs(parsed.query).get('target', [None])[0]
        if target and urlsplit(target).scheme in ('http', 'https'):
            return target
        return None

    if len(path_parts) >= 3 and path_parts[:2] == ['discussion', 'comment']:
        if path_parts[2].isdigit():
            return archive_url(f'/discussion/comment/{path_parts[2]}')
        return None

    if len(path_parts) >= 2 and path_parts[0] == 'discussion' and path_parts[1].isdigit():
        discussion_id = int(path_parts[1])
        comment_fragment = re.fullmatch(r'Comment_(\d+)', parsed.fragment, re.IGNORECASE)
        if comment_fragment:
            return archive_url(f'/discussion/comment/{comment_fragment.group(1)}')
        if parsed.fragment.lower() == 'latest':
            latest_comment = query_db("""
                SELECT comment_id, date_inserted
                FROM comments
                WHERE discussion_id = ?
                  AND (? IS NULL OR date_inserted <= ?)
                ORDER BY date_inserted DESC, comment_id DESC
                LIMIT 1
            """, [discussion_id, reference_date, reference_date], one=True)
            if not latest_comment:
                return archive_url(f'/discussion/{discussion_id}')

            comment_position = query_db("""
                SELECT COUNT(*) AS cnt
                FROM comments
                WHERE discussion_id = ?
                  AND (
                      date_inserted < ?
                      OR (date_inserted = ? AND comment_id <= ?)
                  )
            """, [
                discussion_id,
                latest_comment['date_inserted'],
                latest_comment['date_inserted'],
                latest_comment['comment_id'],
            ], one=True)['cnt']
            page = max(1, (comment_position + 29) // 30)
            local = archive_url(
                f'/discussion/{discussion_id}?page={page}&per_page=30'
            )
            return f'{local}#Comment_{latest_comment["comment_id"]}'
        local = archive_url(f'/discussion/{discussion_id}')
        return f'{local}#{parsed.fragment}' if parsed.fragment else local

    return None


def archive_url(path):
    """Build an absolute archive URL from configuration or the request host."""
    base_url = app.config.get('PUBLIC_URL') or request.url_root
    return f'{base_url.rstrip("/")}{path}'


def rewrite_forum_links(html, reference_date=None):
    """Rewrite links embedded in exported post HTML."""
    if not html:
        return ''

    def replace_anchor(match):
        original_url = match.group(3)
        local = resolve_forum_link(original_url, reference_date)
        if not local:
            return match.group(0)

        body = match.group(5)
        if '<' not in body and unescape(body.strip()) == unescape(original_url):
            leading_space = body[:len(body) - len(body.lstrip())]
            trailing_space = body[len(body.rstrip()):]
            body = f'{leading_space}{escape(local)}{trailing_space}'

        return (
            f'{match.group(1)}{match.group(2)}{escape(local, quote=True)}'
            f'{match.group(2)}{match.group(4)}{body}</a>'
        )

    return re.sub(
        r'(<a\b[^>]*?href\s*=\s*)(["\x27])(.+?)\2([^>]*>)(.*?)</a>',
        replace_anchor,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )


def rewrite_content(html, reference_date=None):
    return rewrite_forum_links(rewrite_images(html), reference_date)


app.jinja_env.filters['format_date'] = format_date
app.jinja_env.filters['rewrite_images'] = rewrite_content


# =============================================================================
# Templates
# =============================================================================

STYLES = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Lato:400,700|Oswald:300,400,500">
<style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
        font-family: 'Lato', sans-serif;
        background: #f5f5f5;
        color: #333;
        line-height: 1.6;
    }
    h1, h2, h3, h4 { font-family: 'Oswald', sans-serif; text-transform: uppercase; }
    .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
    header {
        background: #0f1c3c;
        color: white;
        padding: 0;
        margin-bottom: 30px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.2);
    }
    header .container {
        display: flex; justify-content: space-between; align-items: center;
        padding: 16px 20px;
    }
    header h1 { font-size: 20px; font-weight: 400; letter-spacing: 2px; }
    header h1 a { color: white; text-decoration: none; }
    header nav a {
        color: rgba(255,255,255,0.8); text-decoration: none; margin-left: 20px;
        font-family: 'Oswald', sans-serif; font-size: 14px; text-transform: uppercase; letter-spacing: 1px;
    }
    header nav a:hover { color: #f78e1e; }
    .header-accent {
        height: 4px;
        background: linear-gradient(to right, #f78e1e 50%, #0f1c3c 50%);
    }
    .breadcrumb {
        background: white;
        padding: 12px 20px;
        margin-bottom: 20px;
        font-size: 14px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        border-left: 4px solid #f78e1e;
    }
    .breadcrumb a { color: #0f1c3c; text-decoration: none; }
    .breadcrumb a:hover { color: #f78e1e; }
    .breadcrumb span { color: #999; margin: 0 8px; }
    .category-list { display: flex; flex-direction: column; gap: 12px; }
    .category-card {
        background: white;
        padding: 20px;
        display: flex;
        align-items: center;
        gap: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        transition: transform 0.2s, box-shadow 0.2s;
        border-left: 4px solid #f78e1e;
    }
    .category-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .category-icon {
        width: 50px; height: 50px;
        background: #0f1c3c;
        display: flex; align-items: center; justify-content: center;
        color: white; font-size: 24px; flex-shrink: 0;
    }
    .category-info { flex-grow: 1; }
    .category-info h2 { font-size: 18px; margin-bottom: 4px; font-weight: 400; }
    .category-info h2 a { color: #0f1c3c; text-decoration: none; }
    .category-info h2 a:hover { color: #f78e1e; }
    .category-info p { color: #666; font-size: 14px; text-transform: none; }
    .category-stats { text-align: right; font-size: 13px; color: #777; }
    .category-stats strong { color: #0f1c3c; }
    .discussion-list { background: white; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .discussion-item { padding: 16px 20px; border-bottom: 1px solid #e5e5e5; display: flex; align-items: center; gap: 15px; }
    .discussion-item:last-child { border-bottom: none; }
    .discussion-item:hover { background: #fafafa; }
    .discussion-avatar { width: 40px; height: 40px; border-radius: 50%; background: #ddd; flex-shrink: 0; object-fit: cover; }
    .discussion-content { flex-grow: 1; min-width: 0; }
    .discussion-title { font-size: 15px; font-weight: 500; margin-bottom: 4px; font-family: 'Lato', sans-serif; text-transform: none; }
    .discussion-title a { color: #0f1c3c; text-decoration: none; }
    .discussion-title a:hover { color: #f78e1e; }
    .discussion-meta { font-size: 12px; color: #777; }
    .discussion-meta a { color: #f78e1e; text-decoration: none; }
    .discussion-stats { text-align: right; font-size: 12px; color: #777; flex-shrink: 0; }
    .discussion-stats .count { font-weight: 600; color: #0f1c3c; }
    .badge { display: inline-block; padding: 2px 8px; font-size: 10px; font-weight: 500; margin-right: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
    .badge-pinned { background: #f78e1e; color: white; }
    .badge-closed { background: #777; color: white; }
    .thread-header { background: white; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-left: 4px solid #f78e1e; }
    .thread-header h1 { font-size: 24px; color: #0f1c3c; margin-bottom: 8px; font-weight: 400; letter-spacing: 1px; }
    .thread-meta { font-size: 14px; color: #777; font-family: 'Lato', sans-serif; text-transform: none; }
    .thread-meta a { color: #f78e1e; text-decoration: none; }
    .post { background: white; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }
    .post-header { display: flex; align-items: center; gap: 12px; padding: 16px 20px; border-bottom: 1px solid #e5e5e5; background: #fafafa; }
    .post-avatar { width: 48px; height: 48px; border-radius: 50%; background: #ddd; object-fit: cover; }
    .post-author { flex-grow: 1; }
    .post-author-name { font-weight: 700; color: #0f1c3c; }
    .post-author-name a { color: inherit; text-decoration: none; }
    .post-author-name a:hover { color: #f78e1e; }
    .post-author-title { font-size: 12px; color: #777; }
    .post-date { font-size: 12px; color: #777; }
    .post-body { padding: 20px; }
    .post-body img { max-width: 100%; height: auto; margin: 10px 0; }
    .post-body p { margin-bottom: 1em; }
    .post-body ul, .post-body ol { margin: 1em 0; padding-left: 2em; }
    .post-body li { margin-bottom: 0.5em; }
    .post-body a { color: #f78e1e; }
    .post-body blockquote { border-left: 4px solid #f78e1e; padding-left: 16px; margin: 16px 0; color: #666; }
    .post-body pre, .post-body code { background: #f5f5f5; padding: 2px 6px; font-family: monospace; }
    .post-body pre { padding: 12px; overflow-x: auto; }
    .post.original-post { border-left: 4px solid #f78e1e; }
    .profile-header {
        background: white; padding: 28px; margin-bottom: 20px;
        display: flex; align-items: center; gap: 24px;
        border-left: 4px solid #f78e1e; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .profile-avatar {
        width: 120px; height: 120px; border-radius: 50%; background: #ddd;
        object-fit: cover; flex-shrink: 0;
    }
    .profile-identity { flex-grow: 1; min-width: 0; }
    .profile-identity h1 {
        color: #0f1c3c; font-size: 30px; font-weight: 400;
        letter-spacing: 1px; overflow-wrap: anywhere;
    }
    .profile-title { color: #777; margin-top: 4px; }
    .profile-joined { color: #777; font-size: 13px; margin-top: 8px; }
    .profile-stats { display: flex; gap: 12px; margin-bottom: 20px; }
    .profile-stat {
        background: white; padding: 18px 24px; min-width: 150px;
        text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .profile-stat strong {
        display: block; color: #0f1c3c; font-family: 'Oswald', sans-serif;
        font-size: 24px; font-weight: 400;
    }
    .profile-stat span { color: #777; font-size: 12px; text-transform: uppercase; }
    .profile-activity { background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .profile-activity h2 {
        padding: 16px 20px; color: #0f1c3c; font-size: 18px; font-weight: 400;
        border-bottom: 1px solid #e5e5e5;
    }
    .activity-item { padding: 16px 20px; border-bottom: 1px solid #e5e5e5; }
    .activity-item:last-child { border-bottom: none; }
    .activity-item a { color: #0f1c3c; font-weight: 700; text-decoration: none; }
    .activity-item a:hover { color: #f78e1e; }
    .activity-meta { color: #777; font-size: 12px; margin-top: 3px; }
    .empty-state { text-align: center; padding: 60px 20px; color: #777; }
    .empty-state h2 { color: #0f1c3c; margin-bottom: 8px; }
    footer {
        background: #323232;
        color: #fff;
        text-align: center;
        padding: 0;
        font-size: 13px;
        margin-top: 40px;
        box-shadow: inset 0 0 8px rgba(0,0,0,0.55);
    }
    footer .footer-accent {
        height: 4px;
        background: linear-gradient(to right, #f78e1e 50%, #0f1c3c 50%);
    }
    footer .footer-content { padding: 30px 20px; }
    footer a { color: #f78e1e; text-decoration: none; }
    .pagination {
        display: flex; justify-content: center; align-items: center; gap: 8px;
        margin-top: 20px; padding: 16px; background: white;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .pagination-btn {
        padding: 8px 16px; text-decoration: none;
        color: #0f1c3c; background: #f5f5f5; font-size: 14px;
        font-family: 'Oswald', sans-serif; text-transform: uppercase; letter-spacing: 0.5px;
    }
    .pagination-btn:hover:not(.disabled) { background: #f78e1e; color: white; }
    .pagination-btn.disabled { color: #bbb; cursor: default; }
    .pagination-pages { display: flex; gap: 4px; }
    .pagination-page {
        padding: 8px 12px; text-decoration: none;
        color: #0f1c3c; font-size: 14px;
    }
    .pagination-page:hover { background: #f5f5f5; }
    .pagination-page.current { background: #f78e1e; color: white; }
    .pagination-ellipsis { padding: 8px 4px; color: #777; }
    .pagination-options select {
        padding: 6px 10px; border: 1px solid #ddd;
        font-size: 14px; margin-left: 8px; cursor: pointer;
    }
    @media (max-width: 640px) {
        .profile-header { align-items: flex-start; padding: 20px; }
        .profile-avatar { width: 80px; height: 80px; }
        .profile-identity h1 { font-size: 24px; }
        .profile-stats { flex-direction: column; gap: 8px; }
        .profile-stat { min-width: 0; }
    }
</style>
"""

def base_template(title, content):
    """Wrap content in base template."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {STYLES}
</head>
<body>
    <header>
        <div class="header-accent"></div>
        <div class="container">
            <h1><a href="/">Kissena Forum Archive</a></h1>
            <nav>
                <a href="/">Categories</a>
            </nav>
        </div>
    </header>
    <main class="container">
        {content}
    </main>
    <footer>
        <div class="footer-accent"></div>
        <div class="footer-content">
            Kissena Forum Archive &middot; Exported with vf_export.py
        </div>
    </footer>
</body>
</html>"""


INDEX_TEMPLATE = """
<div class="breadcrumb">
    <strong>Categories</strong>
</div>

{% if categories %}
<div class="category-list">
    {% for cat in categories %}
    <div class="category-card">
        <div class="category-icon">&#128194;</div>
        <div class="category-info">
            <h2><a href="/category/{{ cat.category_id }}">{{ cat.name }}</a></h2>
            {% if cat.description %}
            <p>{{ cat.description }}</p>
            {% endif %}
        </div>
        <div class="category-stats">
            <div><strong>{{ cat.count_discussions or 0 }}</strong> discussions</div>
            <div><strong>{{ cat.count_comments or 0 }}</strong> comments</div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="empty-state">
    <h2>No Categories</h2>
    <p>No categories have been exported yet.</p>
</div>
{% endif %}
"""

CATEGORY_TEMPLATE = """
<div class="breadcrumb">
    <a href="/">Categories</a>
    <span>&rsaquo;</span>
    <strong>{{ category.name }}</strong>
</div>

{% if discussions %}
<div class="discussion-list">
    {% for disc in discussions %}
    <div class="discussion-item">
        {% if disc.avatar_user_id %}
        <img class="discussion-avatar" src="/avatar/{{ disc.avatar_user_id }}" alt="">
        {% else %}
        <div class="discussion-avatar"></div>
        {% endif %}
        <div class="discussion-content">
            <div class="discussion-title">
                {% if disc.pinned %}<span class="badge badge-pinned">Pinned</span>{% endif %}
                {% if disc.closed %}<span class="badge badge-closed">Closed</span>{% endif %}
                <a href="/discussion/{{ disc.discussion_id }}">{{ disc.name }}</a>
            </div>
            <div class="discussion-meta">
                Last reply by
                {% if disc.avatar_user_id %}
                <a href="{{ url_for('profile', user_id=disc.avatar_user_id, username=disc.last_reply_name) }}">{{ disc.last_reply_name or 'Unknown' }}</a>
                {% else %}
                {{ disc.last_reply_name or 'Unknown' }}
                {% endif %}
                &middot; {{ disc.last_activity_date | format_date }}
            </div>
        </div>
        <div class="discussion-stats">
            <div><span class="count">{{ disc.count_comments or 0 }}</span> comments</div>
            <div><span class="count">{{ disc.count_views or 0 }}</span> views</div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="empty-state">
    <h2>No Discussions</h2>
    <p>No discussions in this category.</p>
</div>
{% endif %}
"""

DISCUSSION_TEMPLATE = """
{% macro pagination_controls() %}
{% if total_pages > 1 %}
<div class="pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}&per_page={{ per_page }}" class="pagination-btn">&laquo; Previous</a>
    {% else %}
    <span class="pagination-btn disabled">&laquo; Previous</span>
    {% endif %}

    <span class="pagination-pages">
        {% for p in range(1, total_pages + 1) %}
            {% if p == page %}
                <span class="pagination-page current">{{ p }}</span>
            {% elif p == 1 or p == total_pages or (p >= page - 2 and p <= page + 2) %}
                <a href="?page={{ p }}&per_page={{ per_page }}" class="pagination-page">{{ p }}</a>
            {% elif p == page - 3 or p == page + 3 %}
                <span class="pagination-ellipsis">&hellip;</span>
            {% endif %}
        {% endfor %}
    </span>

    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}&per_page={{ per_page }}" class="pagination-btn">Next &raquo;</a>
    {% else %}
    <span class="pagination-btn disabled">Next &raquo;</span>
    {% endif %}

    <span class="pagination-options">
        <label>Per page:
            <select onchange="window.location.href='?page=1&per_page=' + this.value">
                {% for size in allowed_per_page %}
                <option value="{{ size }}" {{ 'selected' if size == per_page else '' }}>{{ size }}</option>
                {% endfor %}
            </select>
        </label>
    </span>
</div>
{% endif %}
{% endmacro %}

<div class="breadcrumb">
    <a href="/">Categories</a>
    <span>&rsaquo;</span>
    <a href="/category/{{ category.category_id }}">{{ category.name }}</a>
    <span>&rsaquo;</span>
    <strong>{{ discussion.name }}</strong>
</div>

<div class="thread-header">
    <h1>
        {% if discussion.pinned %}<span class="badge badge-pinned">Pinned</span>{% endif %}
        {% if discussion.closed %}<span class="badge badge-closed">Closed</span>{% endif %}
        {{ discussion.name }}
    </h1>
    <div class="thread-meta">
        {{ discussion.count_comments or 0 }} comments &middot;
        {{ discussion.count_views or 0 }} views
    </div>
</div>

<!-- Comments -->
{{ pagination_controls() }}

{% if page == 1 %}
<div class="post">
    <div class="post-header">
        {% if author %}
        <img class="post-avatar" src="/avatar/{{ author.user_id }}" alt="">
        <div class="post-author">
            <div class="post-author-name"><a href="{{ url_for('profile', user_id=author.user_id, username=author.name) }}">{{ author.name }}</a></div>
            {% if author.title %}<div class="post-author-title">{{ author.title }}</div>{% endif %}
        </div>
        {% else %}
        <div class="post-avatar"></div>
        <div class="post-author"><div class="post-author-name">Unknown</div></div>
        {% endif %}
        <div class="post-date">{{ discussion.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">
        {{ discussion.body | rewrite_images(discussion.date_inserted) | safe }}
    </div>
</div>
{% endif %}

{% for comment in comments %}
<div class="post" id="Comment_{{ comment.comment_id }}">
    <div class="post-header">
        {% if comment.user_id %}
        <img class="post-avatar" src="/avatar/{{ comment.user_id }}" alt="">
        <div class="post-author">
            <div class="post-author-name"><a href="{{ url_for('profile', user_id=comment.user_id, username=comment.user_name) }}">{{ comment.user_name or 'Unknown' }}</a></div>
            {% if comment.user_title %}<div class="post-author-title">{{ comment.user_title }}</div>{% endif %}
        </div>
        {% else %}
        <div class="post-avatar"></div>
        <div class="post-author"><div class="post-author-name">Unknown</div></div>
        {% endif %}
        <div class="post-date">{{ comment.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">
        {{ comment.body | rewrite_images(comment.date_inserted) | safe }}
    </div>
</div>
{% endfor %}

{{ pagination_controls() }}
"""

PROFILE_TEMPLATE = """
<div class="breadcrumb">
    <a href="/">Categories</a>
    <span>&rsaquo;</span>
    <strong>{{ user.name }}</strong>
</div>

<section class="profile-header">
    <img class="profile-avatar" src="/avatar/{{ user.user_id }}" alt="{{ user.name }}'s avatar">
    <div class="profile-identity">
        <h1>{{ user.name }}</h1>
        {% if user.title or user.label %}
        <div class="profile-title">{{ user.title or user.label }}</div>
        {% endif %}
        {% if user.date_inserted %}
        <div class="profile-joined">Joined {{ user.date_inserted | format_date }}</div>
        {% endif %}
    </div>
</section>

<div class="profile-stats" aria-label="Contribution counts">
    <div class="profile-stat">
        <strong>{{ user.count_discussions or 0 }}</strong>
        <span>Discussions</span>
    </div>
    <div class="profile-stat">
        <strong>{{ user.count_comments or 0 }}</strong>
        <span>Comments</span>
    </div>
</div>

<section class="profile-activity">
    <h2>Recent activity</h2>
    {% if activity %}
        {% for item in activity %}
        <div class="activity-item">
            {% if item.activity_type == 'discussion' %}
            Started <a href="/discussion/{{ item.discussion_id }}">{{ item.discussion_name }}</a>
            {% else %}
            Commented on <a href="/discussion/comment/{{ item.item_id }}">{{ item.discussion_name }}</a>
            {% endif %}
            {% if item.category_name %} in {{ item.category_name }}{% endif %}
            <div class="activity-meta">{{ item.date_inserted | format_date }}</div>
        </div>
        {% endfor %}
    {% else %}
        <div class="empty-state">
            <p>No archived activity for this user.</p>
        </div>
    {% endif %}
</section>
"""


# =============================================================================
# Routes
# =============================================================================

@app.route('/')
def index():
    """Show all categories."""
    categories = query_db("""
        SELECT * FROM categories
        ORDER BY parent_category_id NULLS FIRST, name
    """)
    content = render_template_string(INDEX_TEMPLATE, categories=categories)
    return base_template("Forum Archive", content)


@app.route('/category/<int:category_id>')
def category(category_id):
    """Show discussions in a category."""
    cat = query_db("SELECT * FROM categories WHERE category_id = ?", [category_id], one=True)
    if not cat:
        abort(404)

    discussions = query_db("""
        SELECT d.*,
               u.name as author_name,
               COALESCE(last_comment.last_user_id, d.insert_user_id) as avatar_user_id,
               COALESCE(last_commenter.name, u.name) as last_reply_name,
               COALESCE(last_comment.last_date, d.date_inserted) as last_activity_date
        FROM discussions d
        LEFT JOIN users u ON d.insert_user_id = u.user_id
        LEFT JOIN (
            SELECT discussion_id,
                   MAX(date_inserted) as last_date,
                   (SELECT insert_user_id FROM comments c2
                    WHERE c2.discussion_id = c.discussion_id
                    ORDER BY date_inserted DESC LIMIT 1) as last_user_id
            FROM comments c
            GROUP BY discussion_id
        ) last_comment ON d.discussion_id = last_comment.discussion_id
        LEFT JOIN users last_commenter ON last_comment.last_user_id = last_commenter.user_id
        WHERE d.category_id = ?
        ORDER BY d.pinned DESC, last_activity_date DESC
    """, [category_id])

    content = render_template_string(CATEGORY_TEMPLATE, category=cat, discussions=discussions)
    return base_template(f"{cat['name']} - Forum Archive", content)


@app.route('/profile/<int:user_id>')
@app.route('/profile/<int:user_id>/<username>')
def profile(user_id, username=None):
    """Show a user's public profile and recent archived activity."""
    user = query_db("""
        SELECT user_id, name, title, label, date_inserted,
               count_discussions, count_comments
        FROM users
        WHERE user_id = ?
    """, [user_id], one=True)
    if not user:
        abort(404)

    activity = query_db("""
        SELECT 'discussion' AS activity_type,
               d.discussion_id AS item_id,
               d.discussion_id,
               d.name AS discussion_name,
               cat.name AS category_name,
               d.date_inserted
        FROM discussions d
        LEFT JOIN categories cat ON cat.category_id = d.category_id
        WHERE d.insert_user_id = ?

        UNION ALL

        SELECT 'comment' AS activity_type,
               c.comment_id AS item_id,
               c.discussion_id,
               d.name AS discussion_name,
               cat.name AS category_name,
               c.date_inserted
        FROM comments c
        JOIN discussions d ON d.discussion_id = c.discussion_id
        LEFT JOIN categories cat ON cat.category_id = d.category_id
        WHERE c.insert_user_id = ?

        ORDER BY date_inserted DESC, item_id DESC
        LIMIT 20
    """, [user_id, user_id])

    content = render_template_string(
        PROFILE_TEMPLATE,
        user=user,
        activity=activity,
    )
    return base_template(f"{user['name']} - Forum Archive", content)


@app.route('/discussion/<int:discussion_id>')
def discussion(discussion_id):
    """Show a discussion thread with comments."""
    disc = query_db("SELECT * FROM discussions WHERE discussion_id = ?", [discussion_id], one=True)
    if not disc:
        abort(404)

    cat = query_db("SELECT * FROM categories WHERE category_id = ?", [disc['category_id']], one=True)
    author = query_db("SELECT * FROM users WHERE user_id = ?", [disc['insert_user_id']], one=True)

    # Pagination
    allowed_per_page = [10, 20, 30, 50, 100]
    per_page = request.args.get('per_page', 30, type=int)
    if per_page not in allowed_per_page:
        per_page = 30
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    total_comments = query_db(
        "SELECT COUNT(*) as cnt FROM comments WHERE discussion_id = ?",
        [discussion_id], one=True
    )['cnt']
    total_pages = max(1, (total_comments + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page

    comments = query_db(f"""
        SELECT c.*, u.name as user_name, u.title as user_title, u.user_id
        FROM comments c
        LEFT JOIN users u ON c.insert_user_id = u.user_id
        WHERE c.discussion_id = ?
        ORDER BY c.date_inserted ASC, c.comment_id ASC
        LIMIT {per_page} OFFSET {offset}
    """, [discussion_id])

    content = render_template_string(
        DISCUSSION_TEMPLATE,
        discussion=disc,
        category=cat,
        author=author,
        comments=comments,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_comments=total_comments,
        allowed_per_page=allowed_per_page
    )
    return base_template(f"{disc['name']} - Forum Archive", content)


@app.route('/discussion/comment/<int:comment_id>', strict_slashes=False)
def comment_permalink(comment_id):
    """Redirect a Vanilla comment permalink to its paginated discussion."""
    comment = query_db(
        "SELECT discussion_id, date_inserted FROM comments WHERE comment_id = ?",
        [comment_id], one=True
    )
    if not comment:
        abort(404)

    preceding_comments = query_db("""
        SELECT COUNT(*) AS cnt
        FROM comments
        WHERE discussion_id = ?
          AND (
              date_inserted < ?
              OR (date_inserted = ? AND comment_id < ?)
          )
    """, [
        comment['discussion_id'],
        comment['date_inserted'],
        comment['date_inserted'],
        comment_id,
    ], one=True)['cnt']

    per_page = 30
    page = preceding_comments // per_page + 1
    return redirect(url_for(
        'discussion',
        discussion_id=comment['discussion_id'],
        page=page,
        per_page=per_page,
        _anchor=f'Comment_{comment_id}',
    ))


@app.route('/image/<int:image_id>')
def image(image_id):
    """Serve an image from filesystem storage."""
    img = query_db(
        "SELECT content_type, file_path FROM images WHERE image_id = ?",
        [image_id], one=True
    )
    if not img or not img['file_path']:
        abort(404)

    file_path = app.config['DATA_DIR'] / img['file_path']
    if not file_path.exists():
        abort(404)

    return send_file(
        file_path,
        mimetype=img['content_type'] or 'image/jpeg'
    )


@app.route('/avatar/<int:user_id>')
def avatar(user_id):
    """Serve a user avatar."""
    av = query_db(
        "SELECT content_type, file_path FROM user_avatars WHERE user_id = ?",
        [user_id], one=True
    )
    if av and av['file_path']:
        file_path = app.config['DATA_DIR'] / av['file_path']
        if file_path.exists():
            return send_file(
                file_path,
                mimetype=av['content_type'] or 'image/jpeg'
            )

    # Placeholder SVG
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <rect fill="#ddd" width="100" height="100"/>
        <circle fill="#bbb" cx="50" cy="40" r="20"/>
        <ellipse fill="#bbb" cx="50" cy="85" rx="30" ry="25"/>
    </svg>'''
    return Response(svg, mimetype='image/svg+xml')


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='View exported Vanilla Forums data')
    parser.add_argument('--db', default='test.db', help='SQLite database path (default: test.db)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run on (default: 5000)')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--public-url',
                        help='Public archive base URL used when rewriting embedded links')

    args = parser.parse_args()

    db_path = Path(args.db)
    app.config['DATABASE'] = args.db
    app.config['DATA_DIR'] = db_path.parent if db_path.parent != Path() else Path('.')
    app.config['PUBLIC_URL'] = args.public_url
    print(f"Starting forum viewer at http://{args.host}:{args.port}")
    print(f"Database: {args.db}")
    print(f"Data directory: {app.config['DATA_DIR']}")
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == '__main__':
    main()

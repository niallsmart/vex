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
from pathlib import Path
from flask import Flask, g, render_template_string, abort, Response, request, send_file

app = Flask(__name__)
app.config['DATABASE'] = 'test.db'
app.config['DATA_DIR'] = Path('.')  # Directory containing images/ and avatars/


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


app.jinja_env.filters['format_date'] = format_date
app.jinja_env.filters['rewrite_images'] = rewrite_images


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
                Last reply by {{ disc.last_reply_name or 'Unknown' }}
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
            <div class="post-author-name">{{ author.name }}</div>
            {% if author.title %}<div class="post-author-title">{{ author.title }}</div>{% endif %}
        </div>
        {% else %}
        <div class="post-avatar"></div>
        <div class="post-author"><div class="post-author-name">Unknown</div></div>
        {% endif %}
        <div class="post-date">{{ discussion.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">
        {{ discussion.body | rewrite_images | safe }}
    </div>
</div>
{% endif %}

{% for comment in comments %}
<div class="post">
    <div class="post-header">
        {% if comment.user_id %}
        <img class="post-avatar" src="/avatar/{{ comment.user_id }}" alt="">
        <div class="post-author">
            <div class="post-author-name">{{ comment.user_name or 'Unknown' }}</div>
            {% if comment.user_title %}<div class="post-author-title">{{ comment.user_title }}</div>{% endif %}
        </div>
        {% else %}
        <div class="post-avatar"></div>
        <div class="post-author"><div class="post-author-name">Unknown</div></div>
        {% endif %}
        <div class="post-date">{{ comment.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">
        {{ comment.body | rewrite_images | safe }}
    </div>
</div>
{% endfor %}

{{ pagination_controls() }}
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
        ORDER BY c.date_inserted ASC
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

    args = parser.parse_args()

    db_path = Path(args.db)
    app.config['DATABASE'] = args.db
    app.config['DATA_DIR'] = db_path.parent if db_path.parent != Path() else Path('.')
    print(f"Starting forum viewer at http://{args.host}:{args.port}")
    print(f"Database: {args.db}")
    print(f"Data directory: {app.config['DATA_DIR']}")
    app.run(host=args.host, port=args.port, debug=True)


if __name__ == '__main__':
    main()

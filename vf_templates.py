"""Shared Jinja templates and presentation helpers for the forum archive."""

from datetime import datetime

from jinja2 import DictLoader, Environment, select_autoescape


AVATAR_PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
    <rect fill="#ddd" width="100" height="100"/>
    <circle fill="#bbb" cx="50" cy="40" r="20"/>
    <ellipse fill="#bbb" cx="50" cy="85" rx="30" ry="25"/>
</svg>
"""


def format_date(date_str):
    """Format an exported ISO date for display, preserving viewer fallbacks."""
    if not date_str or date_str.startswith("-"):
        return ""
    try:
        value = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if value.year <= 1:
            return ""
        return value.strftime("%b %d, %Y at %I:%M %p")
    except (TypeError, ValueError, OverflowError):
        return date_str


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
        background: #0f1c3c; color: white; padding: 0; margin-bottom: 30px;
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
        font-family: 'Oswald', sans-serif; font-size: 14px;
        text-transform: uppercase; letter-spacing: 1px;
    }
    header nav a:hover { color: #f78e1e; }
    .header-accent {
        height: 4px;
        background: linear-gradient(to right, #f78e1e 50%, #0f1c3c 50%);
    }
    .breadcrumb {
        background: white; padding: 12px 20px; margin-bottom: 20px;
        font-size: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        border-left: 4px solid #f78e1e;
    }
    .breadcrumb a { color: #0f1c3c; text-decoration: none; }
    .breadcrumb a:hover { color: #f78e1e; }
    .breadcrumb span { color: #999; margin: 0 8px; }
    .category-list { display: flex; flex-direction: column; gap: 12px; }
    .category-card {
        background: white; padding: 20px; display: flex; align-items: center;
        gap: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        transition: transform 0.2s, box-shadow 0.2s; border-left: 4px solid #f78e1e;
    }
    .category-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .category-icon {
        width: 50px; height: 50px; background: #0f1c3c;
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
    .discussion-item {
        padding: 16px 20px; border-bottom: 1px solid #e5e5e5;
        display: flex; align-items: center; gap: 15px;
    }
    .discussion-item:last-child { border-bottom: none; }
    .discussion-item:hover { background: #fafafa; }
    .discussion-avatar {
        width: 40px; height: 40px; border-radius: 50%; background: #ddd;
        flex-shrink: 0; object-fit: cover;
    }
    .discussion-content { flex-grow: 1; min-width: 0; }
    .discussion-title {
        font-size: 15px; font-weight: 500; margin-bottom: 4px;
        font-family: 'Lato', sans-serif; text-transform: none;
    }
    .discussion-title a { color: #0f1c3c; text-decoration: none; }
    .discussion-title a:hover { color: #f78e1e; }
    .discussion-meta { font-size: 12px; color: #777; }
    .discussion-meta a { color: #f78e1e; text-decoration: none; }
    .discussion-stats { text-align: right; font-size: 12px; color: #777; flex-shrink: 0; }
    .discussion-stats .count { font-weight: 600; color: #0f1c3c; }
    .badge {
        display: inline-block; padding: 2px 8px; font-size: 10px;
        font-weight: 500; margin-right: 6px; text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .badge-pinned { background: #f78e1e; color: white; }
    .badge-closed { background: #777; color: white; }
    .thread-header {
        background: white; padding: 24px; margin-bottom: 20px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08); border-left: 4px solid #f78e1e;
    }
    .thread-header h1 {
        font-size: 24px; color: #0f1c3c; margin-bottom: 8px;
        font-weight: 400; letter-spacing: 1px;
    }
    .thread-meta { font-size: 14px; color: #777; font-family: 'Lato', sans-serif; text-transform: none; }
    .thread-meta a { color: #f78e1e; text-decoration: none; }
    .post { background: white; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }
    .post-header {
        display: flex; align-items: center; gap: 12px; padding: 16px 20px;
        border-bottom: 1px solid #e5e5e5; background: #fafafa;
    }
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
    .empty-state { text-align: center; padding: 60px 20px; color: #777; }
    .empty-state h2 { color: #0f1c3c; margin-bottom: 8px; }
    footer {
        background: #323232; color: #fff; text-align: center; padding: 0;
        font-size: 13px; margin-top: 40px; box-shadow: inset 0 0 8px rgba(0,0,0,0.55);
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
        padding: 8px 16px; text-decoration: none; color: #0f1c3c;
        background: #f5f5f5; font-size: 14px; font-family: 'Oswald', sans-serif;
        text-transform: uppercase; letter-spacing: 0.5px;
    }
    .pagination-btn:hover:not(.disabled) { background: #f78e1e; color: white; }
    .pagination-btn.disabled { color: #bbb; cursor: default; }
    .pagination-pages { display: flex; gap: 4px; }
    .pagination-page { padding: 8px 12px; text-decoration: none; color: #0f1c3c; font-size: 14px; }
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


BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    {{ styles | safe }}
</head>
<body>
    <header>
        <div class="header-accent"></div>
        <div class="container">
            <h1><a href="{{ href_index() }}">Kissena Forum Archive</a></h1>
            <nav><a href="{{ href_index() }}">Categories</a></nav>
        </div>
    </header>
    <main class="container">{% block content %}{% endblock %}</main>
    <footer>
        <div class="footer-accent"></div>
        <div class="footer-content">
            Kissena Forum Archive &middot; Exported with vf_export.py{% if export_timestamp %} on {{ export_timestamp | format_date }}{% endif %}
        </div>
    </footer>
</body>
</html>
"""


INDEX_TEMPLATE = """{% extends "base.html" %}
{% block content %}
<div class="breadcrumb"><strong>Categories</strong></div>
{% if categories %}
<div class="category-list">
    {% for cat in categories %}
    <div class="category-card">
        <div class="category-icon">&#128194;</div>
        <div class="category-info">
            <h2><a href="{{ href_category(cat.category_id) }}">{{ cat.name }}</a></h2>
            {% if cat.description %}<p>{{ cat.description }}</p>{% endif %}
        </div>
        <div class="category-stats">
            <div><strong>{{ cat.count_discussions or 0 }}</strong> discussions</div>
            <div><strong>{{ cat.count_comments or 0 }}</strong> comments</div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="empty-state"><h2>No Categories</h2><p>No categories have been exported yet.</p></div>
{% endif %}
{% endblock %}
"""


CATEGORY_TEMPLATE = """{% extends "base.html" %}
{% block content %}
<div class="breadcrumb">
    <a href="{{ href_index() }}">Categories</a><span>&rsaquo;</span><strong>{{ category.name }}</strong>
</div>
{% if discussions %}
<div class="discussion-list">
    {% for disc in discussions %}
    <div class="discussion-item">
        {% if disc.avatar_user_id %}<img class="discussion-avatar" src="{{ href_avatar(disc.avatar_user_id) }}" alt="">
        {% else %}<div class="discussion-avatar"></div>{% endif %}
        <div class="discussion-content">
            <div class="discussion-title">
                {% if disc.pinned %}<span class="badge badge-pinned">Pinned</span>{% endif %}
                {% if disc.closed %}<span class="badge badge-closed">Closed</span>{% endif %}
                <a href="{{ href_discussion(disc.discussion_id) }}">{{ disc.name }}</a>
            </div>
            <div class="discussion-meta">
                Last reply by
                {% if disc.profile_user_id %}<a href="{{ href_profile(disc.profile_user_id, disc.last_reply_name) }}">{{ disc.last_reply_name or 'Unknown' }}</a>
                {% else %}{{ disc.last_reply_name or 'Unknown' }}{% endif %}
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
<div class="empty-state"><h2>No Discussions</h2><p>No discussions in this category.</p></div>
{% endif %}
{% endblock %}
"""


DISCUSSION_TEMPLATE = """{% extends "base.html" %}
{% macro pagination_controls() %}
{% if total_pages > 1 %}
<div class="pagination">
    {% if page > 1 %}<a href="{{ href_discussion(discussion.discussion_id, page - 1) }}" class="pagination-btn">&laquo; Previous</a>
    {% else %}<span class="pagination-btn disabled">&laquo; Previous</span>{% endif %}
    <span class="pagination-pages">
        {% for p in range(1, total_pages + 1) %}
            {% if p == page %}<span class="pagination-page current">{{ p }}</span>
            {% elif p == 1 or p == total_pages or (p >= page - 2 and p <= page + 2) %}<a href="{{ href_discussion(discussion.discussion_id, p) }}" class="pagination-page">{{ p }}</a>
            {% elif p == page - 3 or p == page + 3 %}<span class="pagination-ellipsis">&hellip;</span>{% endif %}
        {% endfor %}
    </span>
    {% if page < total_pages %}<a href="{{ href_discussion(discussion.discussion_id, page + 1) }}" class="pagination-btn">Next &raquo;</a>
    {% else %}<span class="pagination-btn disabled">Next &raquo;</span>{% endif %}
    {% if allowed_per_page %}
    <span class="pagination-options">
        <label>Per page:
            <select onchange="window.location.href='?page=1&per_page=' + this.value">
                {% for size in allowed_per_page %}<option value="{{ size }}" {{ 'selected' if size == per_page else '' }}>{{ size }}</option>{% endfor %}
            </select>
        </label>
    </span>
    {% endif %}
</div>
{% endif %}
{% endmacro %}

{% block content %}
<div class="breadcrumb">
    <a href="{{ href_index() }}">Categories</a><span>&rsaquo;</span>
    <a href="{{ href_category(category.category_id) }}">{{ category.name }}</a><span>&rsaquo;</span>
    <strong>{{ discussion.name }}</strong>
</div>
<div class="thread-header">
    <h1>
        {% if discussion.pinned %}<span class="badge badge-pinned">Pinned</span>{% endif %}
        {% if discussion.closed %}<span class="badge badge-closed">Closed</span>{% endif %}
        {{ discussion.name }}
    </h1>
    <div class="thread-meta">{{ discussion.count_comments or 0 }} comments &middot; {{ discussion.count_views or 0 }} views</div>
</div>
{{ pagination_controls() }}
{% if page == 1 %}
<div class="post">
    <div class="post-header">
        {% if author %}
        <img class="post-avatar" src="{{ href_avatar(author.user_id) }}" alt="">
        <div class="post-author">
            <div class="post-author-name"><a href="{{ href_profile(author.user_id, author.name) }}">{{ author.name }}</a></div>
            {% if author.title %}<div class="post-author-title">{{ author.title }}</div>{% endif %}
        </div>
        {% else %}<div class="post-avatar"></div><div class="post-author"><div class="post-author-name">Unknown</div></div>{% endif %}
        <div class="post-date">{{ discussion.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">{{ discussion.body | rewrite_content(discussion.date_inserted) | safe }}</div>
</div>
{% endif %}
{% for comment in comments %}
<div class="post" id="Comment_{{ comment.comment_id }}">
    <div class="post-header">
        {% if comment.user_id %}
        <img class="post-avatar" src="{{ href_avatar(comment.user_id) }}" alt="">
        <div class="post-author">
            <div class="post-author-name"><a href="{{ href_profile(comment.user_id, comment.user_name) }}">{{ comment.user_name or 'Unknown' }}</a></div>
            {% if comment.user_title %}<div class="post-author-title">{{ comment.user_title }}</div>{% endif %}
        </div>
        {% else %}<div class="post-avatar"></div><div class="post-author"><div class="post-author-name">Unknown</div></div>{% endif %}
        <div class="post-date">{{ comment.date_inserted | format_date }}</div>
    </div>
    <div class="post-body">{{ comment.body | rewrite_content(comment.date_inserted) | safe }}</div>
</div>
{% endfor %}
{{ pagination_controls() }}
{% endblock %}
"""


PROFILE_TEMPLATE = """{% extends "base.html" %}
{% block content %}
<div class="breadcrumb"><a href="{{ href_index() }}">Categories</a><span>&rsaquo;</span><strong>{{ user.name }}</strong></div>
<section class="profile-header">
    <img class="profile-avatar" src="{{ href_avatar(user.user_id) }}" alt="{{ user.name }}'s avatar">
    <div class="profile-identity">
        <h1>{{ user.name }}</h1>
        {% if user.title or user.label %}<div class="profile-title">{{ user.title or user.label }}</div>{% endif %}
        {% set joined_date = user.date_inserted | format_date %}
        {% if joined_date %}<div class="profile-joined">Joined {{ joined_date }}</div>{% endif %}
    </div>
</section>
<div class="profile-stats" aria-label="Contribution counts">
    <div class="profile-stat"><strong>{{ user.count_discussions or 0 }}</strong><span>Discussions</span></div>
    <div class="profile-stat"><strong>{{ user.count_comments or 0 }}</strong><span>Comments</span></div>
</div>
{% endblock %}
"""


TEMPLATES = {
    "base.html": BASE_TEMPLATE,
    "index.html": INDEX_TEMPLATE,
    "category.html": CATEGORY_TEMPLATE,
    "discussion.html": DISCUSSION_TEMPLATE,
    "profile.html": PROFILE_TEMPLATE,
}


def create_environment(rewrite_content, **url_helpers):
    """Create the shared Jinja environment with consumer-specific URL helpers."""
    environment = Environment(
        loader=DictLoader(TEMPLATES),
        autoescape=select_autoescape(("html", "xml"), default_for_string=True),
    )
    environment.filters["format_date"] = format_date
    environment.filters["rewrite_content"] = rewrite_content
    environment.globals.update(url_helpers)
    environment.globals["styles"] = STYLES
    return environment


def render_page(environment, template_name, title, export_timestamp=None, **context):
    """Render one complete archive page."""
    return environment.get_template(template_name).render(
        title=title,
        export_timestamp=export_timestamp,
        **context,
    )

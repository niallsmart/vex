#!/usr/bin/env python3
"""
Vanilla Forums Export Tool

Exports Vanilla Forums data (categories, discussions, comments, users) to a SQLite database
with images stored on the filesystem.

Two-pass export:
  Pass 1: Export categories, discussions, comments, users (fast, API only)
  Pass 2: Download images and avatars (resumable, skips previously failed downloads)

Usage:
    # Full export (both passes)
    python vf_export.py --url https://forum.example.com --token <api_token> --output forum.db

    # Pass 1 only (skip images)
    python vf_export.py --url https://forum.example.com --token <api_token> --output forum.db --skip-images

    # Pass 2 only (download images, resumable)
    python vf_export.py --url https://forum.example.com --token <api_token> --output forum.db --images-only

    # Retry previously failed downloads
    python vf_export.py --url https://forum.example.com --token <api_token> --output forum.db --images-only --retry-failed

    # Test mode (single discussion)
    python vf_export.py --url https://forum.example.com --token <api_token> --output forum.db --discussion 123
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

# Suppress BeautifulSoup's MarkupResemblesLocatorWarning
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


# =============================================================================
# Database Schema
# =============================================================================

SCHEMA = """
-- Categories
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    url_code TEXT,
    parent_category_id INTEGER,
    count_discussions INTEGER,
    count_comments INTEGER,
    raw_json TEXT
);

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    photo_url TEXT,
    title TEXT,
    label TEXT,
    date_inserted TEXT,
    count_discussions INTEGER,
    count_comments INTEGER,
    raw_json TEXT
);

-- User avatars (filesystem storage)
CREATE TABLE IF NOT EXISTS user_avatars (
    user_id INTEGER PRIMARY KEY,
    content_type TEXT,
    file_path TEXT,
    last_error TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Discussions
CREATE TABLE IF NOT EXISTS discussions (
    discussion_id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    body TEXT,
    format TEXT,
    date_inserted TEXT,
    date_updated TEXT,
    insert_user_id INTEGER,
    update_user_id INTEGER,
    count_comments INTEGER,
    count_views INTEGER,
    closed INTEGER,
    pinned INTEGER,
    url TEXT,
    raw_json TEXT,
    FOREIGN KEY (category_id) REFERENCES categories(category_id),
    FOREIGN KEY (insert_user_id) REFERENCES users(user_id)
);

-- Comments
CREATE TABLE IF NOT EXISTS comments (
    comment_id INTEGER PRIMARY KEY,
    discussion_id INTEGER NOT NULL,
    body TEXT,
    format TEXT,
    date_inserted TEXT,
    date_updated TEXT,
    insert_user_id INTEGER,
    raw_json TEXT,
    FOREIGN KEY (discussion_id) REFERENCES discussions(discussion_id),
    FOREIGN KEY (insert_user_id) REFERENCES users(user_id)
);

-- Images extracted from content (filesystem storage)
CREATE TABLE IF NOT EXISTS images (
    image_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT UNIQUE NOT NULL,
    content_type TEXT,
    file_path TEXT,
    downloaded_at TEXT,
    last_error TEXT
);

-- Mapping images to content
CREATE TABLE IF NOT EXISTS content_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL,
    content_id INTEGER NOT NULL,
    image_id INTEGER NOT NULL,
    FOREIGN KEY (image_id) REFERENCES images(image_id)
);

-- Export metadata
CREATE TABLE IF NOT EXISTS export_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_discussions_category ON discussions(category_id);
CREATE INDEX IF NOT EXISTS idx_comments_discussion ON comments(discussion_id);
CREATE INDEX IF NOT EXISTS idx_content_images_content ON content_images(content_type, content_id);
"""


# =============================================================================
# Rate Limiter
# =============================================================================

class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, requests_per_second: float):
        self.rate = requests_per_second
        self.tokens = requests_per_second
        self.last_update = time.monotonic()

    def acquire(self):
        """Wait until a request can be made."""
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
        self.last_update = now

        if self.tokens < 1:
            sleep_time = (1 - self.tokens) / self.rate
            time.sleep(sleep_time)
            self.tokens = 0
            self.last_update = time.monotonic()
        else:
            self.tokens -= 1


# =============================================================================
# Vanilla API Client
# =============================================================================

class VanillaAPIClient:
    """Client for Vanilla Forums API with rate limiting and pagination."""

    def __init__(self, base_url: str, token: str, rate_limit: float = 5.0, trace: bool = False):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.trace = trace
        self.rate_limiter = RateLimiter(rate_limit)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
        })

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make a rate-limited API request with retry on 429."""
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        max_retries = 3

        for attempt in range(max_retries):
            self.rate_limiter.acquire()
            if self.trace:
                print(f"API {method} {url}", file=sys.stderr)
            response = self.session.request(method, url, **kwargs)

            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                print(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            return response

        raise Exception(f"Max retries exceeded for {url}")

    def _get_json(self, endpoint: str, params: Optional[dict] = None) -> Any:
        """GET request returning JSON."""
        response = self._request('GET', endpoint, params=params)
        return response.json()

    def _paginate(self, endpoint: str, params: Optional[dict] = None) -> Generator[dict, None, None]:
        """Yield items from a paginated endpoint."""
        params = params or {}
        params.setdefault('limit', 100)
        page = 1

        while True:
            params['page'] = page
            response = self._request('GET', endpoint, params=params)
            data = response.json()

            if not data:
                break

            for item in data:
                yield item

            # Check for more pages via Link header or response length
            link_header = response.headers.get('Link', '')
            if 'rel="next"' not in link_header and len(data) < params['limit']:
                break

            page += 1

    def get_categories(self) -> list[dict]:
        """Fetch all categories."""
        return list(self._paginate('/api/v2/categories'))

    def get_category(self, category_id: int) -> dict:
        """Fetch a single category."""
        return self._get_json(f'/api/v2/categories/{category_id}')

    def get_discussions(self, category_id: Optional[int] = None) -> Generator[dict, None, None]:
        """Fetch discussions, optionally filtered by category."""
        params = {}
        if category_id is not None:
            params['categoryID'] = category_id
        yield from self._paginate('/api/v2/discussions', params)

    def get_discussion(self, discussion_id: int) -> dict:
        """Fetch a single discussion."""
        return self._get_json(f'/api/v2/discussions/{discussion_id}')

    def get_comments(self, discussion_id: int) -> Generator[dict, None, None]:
        """Fetch comments for a discussion."""
        yield from self._paginate('/api/v2/comments', {'discussionID': discussion_id})

    def get_user(self, user_id: int) -> dict:
        """Fetch a single user."""
        return self._get_json(f'/api/v2/users/{user_id}')

    def download_image(self, url: str) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
        """Download an image, returning (data, content_type, error).

        Returns:
            (data, content_type, None) on success
            (None, None, error_message) on failure
        """
        try:
            self.rate_limiter.acquire()
            # Use requests.get directly (not self.session) to avoid sending
            # the API auth header to external CDNs which may reject it
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', 'application/octet-stream')
            return response.content, content_type, None
        except requests.exceptions.HTTPError as e:
            return None, None, f"HTTP {e.response.status_code}"
        except requests.exceptions.Timeout:
            return None, None, "Timeout"
        except requests.exceptions.ConnectionError as e:
            return None, None, f"Connection error"
        except Exception as e:
            return None, None, str(e)


# =============================================================================
# Database Manager
# =============================================================================

class DatabaseManager:
    """Manages SQLite database operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.output_dir = Path(db_path).parent
        self.images_dir = self.output_dir / "images"
        self.avatars_dir = self.output_dir / "avatars"
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._init_directories()

    def _init_schema(self):
        """Initialize database schema."""
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _init_directories(self):
        """Create directories for image storage."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.avatars_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash_url(url: str) -> str:
        """Generate a hash for a URL to use as filename."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    @staticmethod
    def _get_extension(content_type: Optional[str], url: str) -> str:
        """Determine file extension from content type or URL."""
        if content_type:
            mime_to_ext = {
                'image/jpeg': '.jpg',
                'image/png': '.png',
                'image/gif': '.gif',
                'image/webp': '.webp',
                'image/svg+xml': '.svg',
                'image/bmp': '.bmp',
                'image/tiff': '.tiff',
            }
            ext = mime_to_ext.get(content_type.split(';')[0].strip())
            if ext:
                return ext
        # Fall back to URL extension
        parsed = urlparse(url)
        path_ext = Path(parsed.path).suffix.lower()
        if path_ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff'):
            return path_ext
        return '.bin'

    def close(self):
        """Close database connection."""
        self.conn.close()

    def upsert_category(self, data: dict):
        """Insert or update a category."""
        self.conn.execute("""
            INSERT OR REPLACE INTO categories
            (category_id, name, description, url_code, parent_category_id,
             count_discussions, count_comments, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['categoryID'],
            data.get('name', ''),
            data.get('description'),
            data.get('urlcode'),
            data.get('parentCategoryID'),
            data.get('countDiscussions', 0),
            data.get('countComments', 0),
            json.dumps(data),
        ))

    def upsert_user(self, data: dict):
        """Insert or update a user."""
        self.conn.execute("""
            INSERT OR REPLACE INTO users
            (user_id, name, photo_url, title, label, date_inserted,
             count_discussions, count_comments, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['userID'],
            data.get('name', ''),
            data.get('photoUrl'),
            data.get('title'),
            data.get('label'),
            data.get('dateInserted'),
            data.get('countDiscussions', 0),
            data.get('countComments', 0),
            json.dumps(data),
        ))

    def upsert_discussion(self, data: dict):
        """Insert or update a discussion."""
        self.conn.execute("""
            INSERT OR REPLACE INTO discussions
            (discussion_id, category_id, name, body, format, date_inserted, date_updated,
             insert_user_id, update_user_id, count_comments, count_views, closed, pinned, url, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['discussionID'],
            data['categoryID'],
            data.get('name', ''),
            data.get('body'),
            data.get('format'),
            data.get('dateInserted'),
            data.get('dateUpdated'),
            data.get('insertUserID'),
            data.get('updateUserID'),
            data.get('countComments', 0),
            data.get('countViews', 0),
            1 if data.get('closed') else 0,
            1 if data.get('pinned') else 0,
            data.get('url'),
            json.dumps(data),
        ))

    def upsert_comment(self, data: dict):
        """Insert or update a comment."""
        self.conn.execute("""
            INSERT OR REPLACE INTO comments
            (comment_id, discussion_id, body, format, date_inserted, date_updated,
             insert_user_id, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['commentID'],
            data['discussionID'],
            data.get('body'),
            data.get('format'),
            data.get('dateInserted'),
            data.get('dateUpdated'),
            data.get('insertUserID'),
            json.dumps(data),
        ))

    def register_image(self, url: str) -> int:
        """Register an image URL for later download, returning its ID."""
        cursor = self.conn.execute("""
            INSERT INTO images (original_url)
            VALUES (?)
            ON CONFLICT(original_url) DO UPDATE SET original_url = excluded.original_url
            RETURNING image_id
        """, (url,))
        return cursor.fetchone()[0]

    def save_image(self, url: str, content_type: Optional[str], data: Optional[bytes]) -> tuple[int, Optional[str]]:
        """Save downloaded image data to filesystem and update database.

        Returns:
            (image_id, file_path) tuple
        """
        file_path = None
        if data:
            ext = self._get_extension(content_type, url)
            url_hash = self._hash_url(url)
            prefix = url_hash[:2]
            filename = f"{url_hash}{ext}"
            subdir = self.images_dir / prefix
            subdir.mkdir(parents=True, exist_ok=True)
            (subdir / filename).write_bytes(data)
            # Store relative path for portability
            file_path = f"images/{prefix}/{filename}"

        cursor = self.conn.execute("""
            UPDATE images
            SET content_type = ?, file_path = ?, downloaded_at = ?, last_error = NULL
            WHERE original_url = ?
            RETURNING image_id
        """, (content_type, file_path, datetime.now(timezone.utc).isoformat(), url))
        row = cursor.fetchone()
        if row:
            return row[0], file_path
        # Fallback: insert if not exists (shouldn't happen in normal flow)
        cursor = self.conn.execute("""
            INSERT INTO images (original_url, content_type, file_path, downloaded_at)
            VALUES (?, ?, ?, ?)
            RETURNING image_id
        """, (url, content_type, file_path, datetime.now(timezone.utc).isoformat()))
        return cursor.fetchone()[0], file_path

    def record_image_error(self, url: str, error: str):
        """Record a download error for an image."""
        self.conn.execute("""
            UPDATE images SET last_error = ? WHERE original_url = ?
        """, (error, url))

    def get_pending_images(self, include_failed: bool = False) -> list[tuple[int, str]]:
        """Get images that need downloading (not downloaded or file missing).

        Args:
            include_failed: If True, retry images that previously failed.
        """
        cursor = self.conn.execute("""
            SELECT image_id, original_url, file_path, last_error FROM images
            ORDER BY image_id
        """)
        pending = []
        for row in cursor.fetchall():
            image_id, url, file_path, last_error = row
            # Skip if already downloaded and file exists
            if file_path and (self.output_dir / file_path).exists():
                continue
            # Skip if previously failed (unless retrying)
            if last_error and not include_failed:
                continue
            pending.append((image_id, url))
        return pending

    def get_pending_avatars(self, include_failed: bool = False) -> list[tuple[int, str]]:
        """Get users whose avatars need downloading (not downloaded or file missing).

        Args:
            include_failed: If True, retry avatars that previously failed.
        """
        cursor = self.conn.execute("""
            SELECT u.user_id, u.photo_url, ua.file_path, ua.last_error
            FROM users u
            LEFT JOIN user_avatars ua ON u.user_id = ua.user_id
            WHERE u.photo_url IS NOT NULL
            ORDER BY u.user_id
        """)
        pending = []
        for row in cursor.fetchall():
            user_id, photo_url, file_path, last_error = row
            # Skip if already downloaded and file exists
            if file_path and (self.output_dir / file_path).exists():
                continue
            # Skip if previously failed (unless retrying)
            if last_error and not include_failed:
                continue
            pending.append((user_id, photo_url))
        return pending

    def get_failed_image_count(self) -> int:
        """Get count of images that failed to download."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM images WHERE last_error IS NOT NULL")
        return cursor.fetchone()[0]

    def get_failed_avatar_count(self) -> int:
        """Get count of avatars that failed to download."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM user_avatars WHERE last_error IS NOT NULL")
        return cursor.fetchone()[0]

    def add_content_image(self, content_type: str, content_id: int, image_id: int):
        """Link an image to content."""
        self.conn.execute("""
            INSERT OR IGNORE INTO content_images (content_type, content_id, image_id)
            VALUES (?, ?, ?)
        """, (content_type, content_id, image_id))

    def upsert_user_avatar(self, user_id: int, url: str, content_type: Optional[str], data: Optional[bytes]) -> Optional[str]:
        """Insert or update a user avatar.

        Returns:
            file_path if saved, None otherwise
        """
        file_path = None
        if data:
            ext = self._get_extension(content_type, url)
            filename = f"{user_id}{ext}"
            full_path = self.avatars_dir / filename
            full_path.write_bytes(data)
            # Store relative path for portability
            file_path = f"avatars/{filename}"

        self.conn.execute("""
            INSERT OR REPLACE INTO user_avatars (user_id, content_type, file_path, last_error)
            VALUES (?, ?, ?, NULL)
        """, (user_id, content_type, file_path))
        return file_path

    def record_avatar_error(self, user_id: int, error: str):
        """Record a download error for a user avatar."""
        self.conn.execute("""
            INSERT INTO user_avatars (user_id, last_error)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_error = excluded.last_error
        """, (user_id, error))

    def set_meta(self, key: str, value: str):
        """Set export metadata."""
        self.conn.execute("""
            INSERT OR REPLACE INTO export_meta (key, value) VALUES (?, ?)
        """, (key, value))

    def get_meta(self, key: str) -> Optional[str]:
        """Get export metadata."""
        cursor = self.conn.execute("SELECT value FROM export_meta WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None

    def image_exists(self, url: str) -> bool:
        """Check if an image URL has already been downloaded."""
        cursor = self.conn.execute(
            "SELECT file_path FROM images WHERE original_url = ?", (url,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return False
        # Verify file actually exists on disk
        return (self.output_dir / row[0]).exists()

    def user_exists(self, user_id: int) -> bool:
        """Check if a user already exists in the database."""
        cursor = self.conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

    def user_avatar_exists(self, user_id: int) -> bool:
        """Check if a user avatar has already been downloaded."""
        cursor = self.conn.execute(
            "SELECT file_path FROM user_avatars WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return False
        # Verify file actually exists on disk
        return (self.output_dir / row[0]).exists()

    def commit(self):
        """Commit current transaction."""
        self.conn.commit()


# =============================================================================
# Content Processor
# =============================================================================

class ContentProcessor:
    """Extracts image URLs from HTML content."""

    @staticmethod
    def extract_image_urls(html: Optional[str], source: str = "") -> list[str]:
        """Extract all image URLs from HTML content.

        Args:
            html: HTML content to parse
            source: Source identifier for logging (e.g., "discussion 123")
        """
        if not html:
            return []

        urls = []
        soup = BeautifulSoup(html, 'html.parser')

        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                urls.append(src)

        return urls


# =============================================================================
# Exporter
# =============================================================================

class Exporter:
    """Main export orchestrator with two-pass support."""

    def __init__(self, client: VanillaAPIClient, db: DatabaseManager):
        self.client = client
        self.db = db
        self.user_ids: set[int] = set()

    # =========================================================================
    # Pass 1: Data Export (categories, discussions, comments, users)
    # =========================================================================

    def export_data(self):
        """Pass 1: Export all data except images."""
        print("Pass 1: Exporting data...")

        # 1. Categories
        print("\n[1/4] Exporting categories...")
        categories = self.client.get_categories()
        for cat in categories:
            self.db.upsert_category(cat)
        self.db.commit()
        print(f"  Exported {len(categories)} categories")

        # 2. Discussions and comments
        print("\n[2/4] Exporting discussions and comments...")
        discussion_count = 0
        comment_count = 0

        for cat in categories:
            cat_id = cat['categoryID']
            cat_name = cat.get('name', 'Unknown')
            print(f"  Category: {cat_name} (ID: {cat_id})")

            for disc in self.client.get_discussions(cat_id):
                self._process_discussion(disc)
                discussion_count += 1

                for comment in self.client.get_comments(disc['discussionID']):
                    self._process_comment(comment)
                    comment_count += 1

                if discussion_count % 10 == 0:
                    self.db.commit()
                    print(f"    Progress: {discussion_count} discussions, {comment_count} comments")

        self.db.commit()
        print(f"  Total: {discussion_count} discussions, {comment_count} comments")

        # 3. Users
        self._export_users()

        # 4. Metadata
        self._save_metadata()

        print("\nPass 1 complete!")

    def export_data_for_discussions(self, discussion_ids: list[int]):
        """Pass 1 for specific discussions (test mode)."""
        print(f"Pass 1: Exporting {len(discussion_ids)} discussion(s)...")

        # 1. Fetch and store discussions
        print("\n[1/4] Fetching discussions...")
        category_ids = set()
        for discussion_id in discussion_ids:
            disc = self.client.get_discussion(discussion_id)
            self._process_discussion(disc)
            category_ids.add(disc['categoryID'])
            print(f"  Discussion {discussion_id}: {disc.get('name', 'Unknown')}")
        self.db.commit()

        # 2. Fetch and store categories
        print("\n[2/4] Fetching categories...")
        for category_id in category_ids:
            cat = self.client.get_category(category_id)
            self.db.upsert_category(cat)
            print(f"  Category: {cat.get('name', 'Unknown')}")
        self.db.commit()

        # 3. Fetch and store comments
        print("\n[3/4] Fetching comments...")
        total_comments = 0
        for discussion_id in discussion_ids:
            comment_count = 0
            for comment in self.client.get_comments(discussion_id):
                self._process_comment(comment)
                comment_count += 1
            total_comments += comment_count
            print(f"  Discussion {discussion_id}: {comment_count} comments")
        self.db.commit()
        print(f"  Total: {total_comments} comments")

        # 4. Users
        self._export_users()

        # 5. Metadata
        self._save_metadata()

        print("\nPass 1 complete!")

    def _process_discussion(self, disc: dict):
        """Process a discussion: store data and register images."""
        self.db.upsert_discussion(disc)

        # Track users
        if disc.get('insertUserID'):
            self.user_ids.add(disc['insertUserID'])
        if disc.get('updateUserID'):
            self.user_ids.add(disc['updateUserID'])

        # Register images for later download
        source = f"discussion {disc['discussionID']}"
        for url in ContentProcessor.extract_image_urls(disc.get('body'), source):
            image_id = self.db.register_image(url)
            self.db.add_content_image('discussion', disc['discussionID'], image_id)

    def _process_comment(self, comment: dict):
        """Process a comment: store data and register images."""
        self.db.upsert_comment(comment)

        # Track users
        if comment.get('insertUserID'):
            self.user_ids.add(comment['insertUserID'])

        # Register images for later download
        source = f"comment {comment['commentID']}"
        for url in ContentProcessor.extract_image_urls(comment.get('body'), source):
            image_id = self.db.register_image(url)
            self.db.add_content_image('comment', comment['commentID'], image_id)

    def _export_users(self):
        """Fetch and store all tracked users."""
        print(f"\n[3/4] Exporting {len(self.user_ids)} users...")
        count = 0
        skipped = 0
        for user_id in self.user_ids:
            if self.db.user_exists(user_id):
                skipped += 1
                continue
            try:
                user = self.client.get_user(user_id)
                self.db.upsert_user(user)
                count += 1
                if count % 50 == 0:
                    self.db.commit()
                    print(f"    Progress: {count} fetched, {skipped} skipped")
            except Exception as e:
                print(f"  Failed to fetch user {user_id}: {e}")
        self.db.commit()
        print(f"  Exported {count} users ({skipped} already in database)")

    def _save_metadata(self):
        """Save export metadata."""
        self.db.set_meta('export_timestamp', datetime.now(timezone.utc).isoformat())
        self.db.set_meta('source_url', self.client.base_url)
        self.db.commit()

    # =========================================================================
    # Pass 2: Image Download (resumable)
    # =========================================================================

    def export_images(self, retry_failed: bool = False):
        """Pass 2: Download all pending images and avatars (resumable).

        Args:
            retry_failed: If True, retry images/avatars that previously failed.
        """
        print("Pass 2: Downloading images...")

        # 0. Scan for unregistered images
        self._register_missing_images()

        # 1. Content images
        self._download_pending_images(retry_failed)

        # 2. User avatars
        self._download_pending_avatars(retry_failed)

        # Report failed counts
        failed_images = self.db.get_failed_image_count()
        failed_avatars = self.db.get_failed_avatar_count()
        if failed_images or failed_avatars:
            print(f"\nNote: {failed_images} images and {failed_avatars} avatars have errors.")
            print("      Use --retry-failed to retry them.")

        print("\nPass 2 complete!")

    def _register_missing_images(self):
        """Scan discussions and comments for unregistered images."""
        print("\n[0/2] Scanning for unregistered images...")
        registered = 0

        # Scan discussions
        cursor = self.db.conn.execute("SELECT discussion_id, body FROM discussions WHERE body IS NOT NULL")
        for row in cursor.fetchall():
            discussion_id, body = row
            source = f"discussion {discussion_id}"
            for url in ContentProcessor.extract_image_urls(body, source):
                if not self._image_registered(url):
                    image_id = self.db.register_image(url)
                    self.db.add_content_image('discussion', discussion_id, image_id)
                    registered += 1

        # Scan comments
        cursor = self.db.conn.execute("SELECT comment_id, body FROM comments WHERE body IS NOT NULL")
        for row in cursor.fetchall():
            comment_id, body = row
            source = f"comment {comment_id}"
            for url in ContentProcessor.extract_image_urls(body, source):
                if not self._image_registered(url):
                    image_id = self.db.register_image(url)
                    self.db.add_content_image('comment', comment_id, image_id)
                    registered += 1

        self.db.commit()
        if registered:
            print(f"  Registered {registered} new images")
        else:
            print("  No new images found")

    def _image_registered(self, url: str) -> bool:
        """Check if an image URL is already registered in the database."""
        cursor = self.db.conn.execute(
            "SELECT 1 FROM images WHERE original_url = ?", (url,))
        return cursor.fetchone() is not None

    def _download_pending_images(self, include_failed: bool = False):
        """Download images that haven't been fetched yet."""
        pending = self.db.get_pending_images(include_failed)
        print(f"\n[1/2] Downloading {len(pending)} pending images...")

        downloaded = 0
        failed = 0

        for image_id, url in pending:
            # Double-check file doesn't exist (resumable)
            if self.db.image_exists(url):
                continue

            data, content_type, error = self.client.download_image(url)
            if data:
                _, file_path = self.db.save_image(url, content_type, data)
                print(f"  [ok] {url} -> {file_path}")
                downloaded += 1
            else:
                self.db.record_image_error(url, error or "Unknown error")
                print(f"  [error] {url}: {error}")
                failed += 1

            if (downloaded + failed) % 20 == 0:
                self.db.commit()
                print(f"    Progress: {downloaded} downloaded, {failed} failed")

        self.db.commit()
        print(f"  Downloaded {downloaded} images ({failed} failed)")

    def _download_pending_avatars(self, include_failed: bool = False):
        """Download avatars that haven't been fetched yet."""
        pending = self.db.get_pending_avatars(include_failed)
        print(f"\n[2/2] Downloading {len(pending)} pending avatars...")

        downloaded = 0
        failed = 0

        for user_id, photo_url in pending:
            # Double-check file doesn't exist (resumable)
            if self.db.user_avatar_exists(user_id):
                continue

            data, content_type, error = self.client.download_image(photo_url)
            if data:
                file_path = self.db.upsert_user_avatar(user_id, photo_url, content_type, data)
                print(f"  [ok] user {user_id} -> {file_path}")
                downloaded += 1
            else:
                self.db.record_avatar_error(user_id, error or "Unknown error")
                print(f"  [error] user {user_id}: {error}")
                failed += 1

            if (downloaded + failed) % 20 == 0:
                self.db.commit()
                print(f"    Progress: {downloaded} downloaded, {failed} failed")

        self.db.commit()
        print(f"  Downloaded {downloaded} avatars ({failed} failed)")

    # =========================================================================
    # Full Export (both passes)
    # =========================================================================

    def export_full(self, retry_failed: bool = False):
        """Run full export: both passes."""
        self.export_data()
        self.export_images(retry_failed=retry_failed)
        print("\nFull export complete!")

    def export_discussions(self, discussion_ids: list[int], retry_failed: bool = False):
        """Export specific discussions (test mode): both passes."""
        self.export_data_for_discussions(discussion_ids)
        self.export_images(retry_failed=retry_failed)
        print("\nExport complete!")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Export Vanilla Forums data to SQLite database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Full export (both passes):
    python vf_export.py --url https://forum.example.com --token abc123 --output forum.db

  Pass 1 only (data, skip images):
    python vf_export.py --url https://forum.example.com --token abc123 --output forum.db --skip-images

  Pass 2 only (download images, resumable):
    python vf_export.py --url https://forum.example.com --token abc123 --output forum.db --images-only

  Test mode (single discussion):
    python vf_export.py --url https://forum.example.com --token abc123 --output test.db --discussion 12345

  With token file:
    python vf_export.py --url https://forum.example.com --token-file token.txt --output forum.db
        """
    )

    parser.add_argument('--url', required=True, help='Vanilla Forums base URL')
    parser.add_argument('--token', help='API Bearer token')
    parser.add_argument('--token-file', help='Path to file containing API token')
    parser.add_argument('--output', required=True, help='SQLite database output path')
    parser.add_argument('--rate-limit', type=float, default=5.0,
                        help='Max requests per second (default: 5)')
    parser.add_argument('--discussion',
                        help='Test mode: export specific discussion ID(s), comma-separated')
    parser.add_argument('--skip-images', action='store_true',
                        help='Pass 1 only: export data, skip image downloads')
    parser.add_argument('--images-only', action='store_true',
                        help='Pass 2 only: download pending images (resumable)')
    parser.add_argument('--retry-failed', action='store_true',
                        help='Retry images/avatars that previously failed to download')
    parser.add_argument('--trace-api', action='store_true',
                        help='Log each API call to stderr')

    args = parser.parse_args()

    # Validate mutually exclusive options
    if args.skip_images and args.images_only:
        parser.error('--skip-images and --images-only are mutually exclusive')

    # Get token
    token = args.token
    if not token and args.token_file:
        with open(args.token_file, 'r') as f:
            token = f.read().strip()
    if not token:
        parser.error('Either --token or --token-file is required')

    # Initialize
    client = VanillaAPIClient(args.url, token, args.rate_limit, trace=args.trace_api)
    db = DatabaseManager(args.output)

    try:
        exporter = Exporter(client, db)

        if args.images_only:
            # Pass 2 only: download pending images
            exporter.export_images(retry_failed=args.retry_failed)
        elif args.discussion:
            # Test mode with specific discussions
            discussion_ids = [int(x.strip()) for x in args.discussion.split(',')]
            if args.skip_images:
                exporter.export_data_for_discussions(discussion_ids)
            else:
                exporter.export_discussions(discussion_ids, retry_failed=args.retry_failed)
        elif args.skip_images:
            # Pass 1 only: data export
            exporter.export_data()
        else:
            # Full export: both passes
            exporter.export_full(retry_failed=args.retry_failed)
    finally:
        db.close()


if __name__ == '__main__':
    main()

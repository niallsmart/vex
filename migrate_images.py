#!/usr/bin/env python3
"""
Migration script: BLOB storage to filesystem storage

Migrates an existing vf_export database from BLOB-based image storage
to filesystem-based storage.

Usage:
    python migrate_images.py forum.db

This will:
1. Create images/ and avatars/ directories alongside the database
2. Extract all image BLOBs to the filesystem
3. Alter the schema to use file_path instead of image_data
4. Remove the BLOB data from the database

A backup is created automatically before migration.
"""

import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse


def hash_url(url: str) -> str:
    """Generate a hash for a URL to use as filename."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def get_extension(content_type: str | None, url: str) -> str:
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


def check_already_migrated(conn: sqlite3.Connection) -> bool:
    """Check if database has already been migrated."""
    cursor = conn.execute("PRAGMA table_info(images)")
    columns = {row[1] for row in cursor.fetchall()}
    return 'file_path' in columns and 'image_data' not in columns


def check_needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if database has the old BLOB schema."""
    cursor = conn.execute("PRAGMA table_info(images)")
    columns = {row[1] for row in cursor.fetchall()}
    return 'image_data' in columns


def migrate_images(conn: sqlite3.Connection, images_dir: Path) -> int:
    """Extract images from BLOBs to filesystem, return count."""
    images_dir.mkdir(parents=True, exist_ok=True)

    cursor = conn.execute("""
        SELECT image_id, original_url, content_type, image_data
        FROM images
        WHERE image_data IS NOT NULL
    """)

    count = 0
    updates = []

    for row in cursor:
        image_id, url, content_type, data = row
        ext = get_extension(content_type, url)
        url_hash = hash_url(url)
        prefix = url_hash[:2]
        filename = f"{url_hash}{ext}"
        subdir = images_dir / prefix
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / filename).write_bytes(data)
        updates.append((f"images/{prefix}/{filename}", image_id))
        count += 1

        if count % 100 == 0:
            print(f"  Extracted {count} images...")

    # Add file_path column if it doesn't exist
    cursor = conn.execute("PRAGMA table_info(images)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'file_path' not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN file_path TEXT")

    # Update file paths
    conn.executemany("UPDATE images SET file_path = ? WHERE image_id = ?", updates)

    return count


def migrate_avatars(conn: sqlite3.Connection, avatars_dir: Path) -> int:
    """Extract avatars from BLOBs to filesystem, return count."""
    avatars_dir.mkdir(parents=True, exist_ok=True)

    # Check if user_avatars table exists
    cursor = conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='user_avatars'
    """)
    if not cursor.fetchone():
        return 0

    # Get the photo_url from users table for extension detection
    cursor = conn.execute("""
        SELECT ua.user_id, ua.content_type, ua.image_data, u.photo_url
        FROM user_avatars ua
        JOIN users u ON ua.user_id = u.user_id
        WHERE ua.image_data IS NOT NULL
    """)

    count = 0
    updates = []

    for row in cursor:
        user_id, content_type, data, photo_url = row
        ext = get_extension(content_type, photo_url or '')
        filename = f"{user_id}{ext}"
        file_path = avatars_dir / filename

        file_path.write_bytes(data)
        updates.append((f"avatars/{filename}", user_id))
        count += 1

        if count % 100 == 0:
            print(f"  Extracted {count} avatars...")

    # Add file_path column if it doesn't exist
    cursor = conn.execute("PRAGMA table_info(user_avatars)")
    columns = {row[1] for row in cursor.fetchall()}
    if 'file_path' not in columns:
        conn.execute("ALTER TABLE user_avatars ADD COLUMN file_path TEXT")

    # Update file paths
    conn.executemany("UPDATE user_avatars SET file_path = ? WHERE user_id = ?", updates)

    return count


def drop_blob_columns(conn: sqlite3.Connection):
    """Remove image_data columns by recreating tables without them."""

    # Recreate images table without image_data (add last_error column)
    conn.executescript("""
        -- Recreate images table
        CREATE TABLE images_new (
            image_id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_url TEXT UNIQUE NOT NULL,
            content_type TEXT,
            file_path TEXT,
            downloaded_at TEXT,
            last_error TEXT
        );

        INSERT INTO images_new (image_id, original_url, content_type, file_path, downloaded_at)
        SELECT image_id, original_url, content_type, file_path, downloaded_at FROM images;

        DROP TABLE images;
        ALTER TABLE images_new RENAME TO images;

        -- Recreate user_avatars table (add last_error column)
        CREATE TABLE user_avatars_new (
            user_id INTEGER PRIMARY KEY,
            content_type TEXT,
            file_path TEXT,
            last_error TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        INSERT INTO user_avatars_new (user_id, content_type, file_path)
        SELECT user_id, content_type, file_path FROM user_avatars;

        DROP TABLE user_avatars;
        ALTER TABLE user_avatars_new RENAME TO user_avatars;
    """)


def main():
    parser = argparse.ArgumentParser(
        description='Migrate vf_export database from BLOB to filesystem storage',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example:
    python migrate_images.py forum.db

This creates:
    forum.db.backup  (backup of original)
    images/          (extracted content images)
    avatars/         (extracted user avatars)
        """
    )
    parser.add_argument('database', help='Path to SQLite database')
    parser.add_argument('--no-backup', action='store_true',
                        help='Skip creating backup (not recommended)')
    parser.add_argument('--keep-blobs', action='store_true',
                        help='Keep BLOB columns after migration (for verification)')

    args = parser.parse_args()

    db_path = Path(args.database)
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = db_path.parent
    images_dir = output_dir / "images"
    avatars_dir = output_dir / "avatars"

    # Connect and check state
    conn = sqlite3.connect(db_path)

    if check_already_migrated(conn):
        print("Database has already been migrated to filesystem storage.")
        conn.close()
        sys.exit(0)

    if not check_needs_migration(conn):
        print("Error: Database schema not recognized.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # Create backup
    if not args.no_backup:
        backup_path = db_path.with_suffix('.db.backup')
        print(f"Creating backup: {backup_path}")
        conn.close()
        shutil.copy2(db_path, backup_path)
        conn = sqlite3.connect(db_path)

    print(f"\nMigrating database: {db_path}")

    # Migrate images
    print("\n[1/3] Extracting images...")
    image_count = migrate_images(conn, images_dir)
    print(f"  Extracted {image_count} images to {images_dir}/")

    # Migrate avatars
    print("\n[2/3] Extracting avatars...")
    avatar_count = migrate_avatars(conn, avatars_dir)
    print(f"  Extracted {avatar_count} avatars to {avatars_dir}/")

    conn.commit()

    # Drop BLOB columns
    if not args.keep_blobs:
        print("\n[3/3] Removing BLOB columns...")
        drop_blob_columns(conn)
        conn.commit()

        # Vacuum to reclaim space
        print("  Running VACUUM to reclaim space...")
        conn.execute("VACUUM")
        print("  Done.")
    else:
        print("\n[3/3] Skipping BLOB removal (--keep-blobs)")

    conn.close()

    print(f"\nMigration complete!")
    print(f"  Images: {image_count}")
    print(f"  Avatars: {avatar_count}")
    if not args.no_backup:
        print(f"  Backup: {backup_path}")


if __name__ == '__main__':
    main()

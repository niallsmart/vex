# Static site generator for the Kissena forum archive

## Context

`vf_viewer.py` is a Flask app that renders an exported Vanilla forum from `vanilla.db`, currently hosted on an EC2 instance (deployed by `just sync`). The archive is read-only: there are no forms, POST handlers, or user state. Running a live server for it means paying roughly $7.50/month for a t3.micro and patching an internet-facing host to serve content that changes only when a new export is made.

The goal is to add a deterministic static build published to a private S3 bucket behind CloudFront. A new `vf_static.py` renders the archive to disk and deployment uploads it with explicit cache metadata. The EC2 viewer and its existing `sync` recipe remain available so both hosting approaches can run in parallel during migration or afterward.

The static build must preserve the current viewer's rendered content and navigation, including profile pages, comment anchors, and historical Vanilla-link rewriting added after the original version of this plan. Existing external links to the Flask route scheme do not need to remain compatible.

**Scale (measured from `vanilla.db`):**

| | count |
|---|---:|
| categories | 7 |
| discussions | 533 |
| comments | 121,985 |
| users / profile pages | 305 |
| discussion pages at 30 comments/page | 4,389 |
| total canonical HTML pages | about 4,702 |
| images downloaded | 14,758 (6.1 GB) |
| images failed and still remote | 1,848 |
| avatar rows / downloaded files | 305 / 302 (17 MB) |

The largest thread has 10,705 comments (357 pages). Assets ship byte-for-byte, so the deployed site is roughly 6.1 GB across about 19,800 objects.

## Approach

### 1. Extract a complete shared template layer into `vf_templates.py`

Move `STYLES`, the base page, `INDEX_TEMPLATE`, `CATEGORY_TEMPLATE`, `DISCUSSION_TEMPLATE`, and `PROFILE_TEMPLATE` out of `vf_viewer.py`. Convert the current Python `base_template()` f-string into a Jinja template as part of the extraction; otherwise Jinja URL helpers placed in the header and navigation would never be evaluated.

Replace all hardcoded paths and Flask-only `url_for()` calls with these Jinja globals:

- `href_index()`
- `href_category(category_id)`
- `href_discussion(discussion_id, page=1, anchor=None)`
- `href_comment(comment_id)`
- `href_profile(user_id, username=None)`
- `href_avatar(user_id)`
- `href_image(image_id)`

Each consumer binds its own implementations. Flask bindings return the current routes, including the optional profile username and comment redirect route. Static bindings return canonical static paths and resolve comment IDs directly to a discussion page plus `#Comment_{id}`.

Use an explicit Jinja environment with HTML autoescaping in the generator. Continue to mark exported post bodies as safe after applying the same content-rewrite behavior as the viewer.

Keep the viewer's current date formatting behavior in the shared helper: missing or negative values, and parsed dates in year 1 or earlier, render as blank so invalid legacy values do not produce a profile "Joined" line. Other unparseable values retain the viewer's existing fallback behavior.

### 2. Static URL scheme

Use flat HTML files because CloudFront's Default Root Object applies only at the distribution root; subdirectory `index.html` behavior would otherwise require edge rewriting.

```text
/index.html
/category/{category_id}.html
/discussion/{discussion_id}.html          # page 1
/discussion/{discussion_id}-p{N}.html     # pages 2..N
/profile/{user_id}.html
/images/{hash[:2]}/{hash}{ext}
/avatars/{user_id}{ext}
/avatar-placeholder.svg
```

All generated navigation and rewritten post-body links use these canonical URLs. A comment permalink becomes the appropriate discussion page with its existing comment fragment; no HTML page is emitted for each of the 121,985 comments.

The `.html` scheme intentionally does not preserve external bookmarks or search results that use the old Flask paths, including `/discussion/comment/{id}` and query-string pagination. Accepting those broken legacy links keeps the CloudFront deployment to a private S3 origin, a Default Root Object, and ordinary cache behaviors without an edge function or redirect-object layer.

### 3. Port content rewriting from the current viewer

Do not replace the current `rewrite_forum_links` behavior with a simpler URL substitution. Preserve all of it:

- source-domain and root-relative discussion URLs;
- numeric, numeric-plus-username, and legacy name-only profile URLs;
- `/discussion/comment/{id}` and `#Comment_{id}` links;
- `#latest`, resolved as of the containing discussion/comment's date;
- `/home/leaving?target=...` external links; and
- replacement of visible anchor text when it is the same as the original URL.

Adapt the resolver to use the static `href_*` bindings. Resolve URL-decoded, name-only profile paths case-insensitively to a user ID, then emit the canonical `/profile/{user_id}.html` path. Precompute comment positions and per-discussion ordered comment metadata so comment IDs and dated `#latest` links can be resolved without an SQL query per anchor. Leave unrecognized source-domain links unchanged.

Keep the current image behavior of stripping remote `srcset` attributes. Preload `{original_url: file_path}` once instead of performing one query per `src` attribute. Only rewrite an image URL when the referenced local file exists and its media type is accepted; otherwise retain the original URL. This prevents saved HTML error responses or missing files from becoming broken local image references.

### 4. Write `vf_static.py`

Open SQLite in read-only URI mode and fail clearly if the database does not exist. Reuse the current route queries and ordering rules, including the profile lookup fields, category last-reply/`avatar_user_id` logic, and the `(date_inserted, comment_id)` ordering used for stable comment pagination.

For each discussion, fetch all comments once and slice them in Python. Fix the page size at 30 in one shared constant; do not expose `--per-page`. The original post appears only on page 1 and is not included in `total_comments`. Remove the per-page selector and its JavaScript while retaining the existing pagination window, previous/next links, and ellipses.

Generate one profile page per user row. Match the current viewer's compact profile page containing identity, joined date when valid, and contribution counts; do not add a separate recent-activity section.

Build into a fresh sibling temporary directory, validate it, and replace the requested output directory. Never render over an old `_site`, because deleted discussions, changed page counts, and removed assets would survive both the local build and `aws s3 sync --delete`.

Copy or hard-link `images/` and `avatars/` from the export without re-encoding. Prefer hard links when the source and staging directory share a filesystem so clean HTML rebuilds do not recopy 6.1 GB; fall back to `shutil.copy2` when linking is unavailable. Always construct the staged asset tree from the current export so removed files cannot linger. Write the fallback avatar SVG as `avatar-placeholder.svg`, and use it when an avatar row is absent, has a NULL path, references a missing file, or has an unacceptable media type.

CLI reference:

- `--db PATH` — SQLite export to read; defaults to `vanilla.db`. The file must already exist and is opened read-only.
- `--output DIR` — destination site directory; defaults to `_site`. The generator builds in a fresh sibling staging directory and replaces this directory only after validation succeeds.
- `--preview` — produce an intentionally incomplete build for local human testing. Generate the index and category pages, but restrict their discussion listings to the discussions selected with `--discussion`. Generate all pages of those discussions and the profiles referenced by them. Symlink the export's complete `images/` and `avatars/` directories into the preview output instead of staging their contents.
- `--discussion ID` — include one discussion in a preview build. The option is repeatable and is valid only with `--preview`; fail if an ID does not exist.
- `-h`, `--help` — show usage and exit.

Preview output may contain links in archived post bodies that target omitted discussions, so it skips the full-site link-integrity check and prints a prominent warning. Write a `THIS_IS_A_PREVIEW` marker at the output root, and make the static deployment recipe refuse to upload a directory containing that marker.

The 30-comment page size is intentionally not configurable because changing it would alter every paginated discussion URL and comment-to-page mapping. Asset handling is likewise part of every complete build rather than a separate CLI mode. Add `_site/`, `_preview/`, and temporary build directories to `.gitignore`.

Use `export_meta.export_timestamp` in the footer and avoid wall-clock timestamps so identical inputs produce deterministic HTML.

### 5. Deploy with explicit metadata

Keep the existing EC2 `sync` recipe in the `Justfile` so the Flask viewer can continue running in parallel when wanted. Add `build`, `preview`, `deploy`, and optionally `deploy-dry-run` recipes for the static site. Configure `preview` with a small maintained set of discussion IDs covering a short thread, an image-heavy thread, and a long paginated thread. Keep the bucket private behind CloudFront Origin Access Control; do not enable S3 website hosting.

Deployment must use separate, scoped sync operations:

1. Upload HTML with a short `Cache-Control` policy and `Content-Type: text/html`.
2. Upload images, avatars, CSS, and other assets with a long-lived immutable policy where filenames are content-addressed. Treat mutable assets such as `avatar-placeholder.svg` separately if necessary.
3. Use `--delete` within the same include/exclude scopes so removed HTML and assets are deleted remotely.
4. Refuse deployment if `THIS_IS_A_PREVIEW` exists in the output root.
5. Invalidate the HTML path families after upload, or explicitly accept the configured HTML TTL delay. The default deployment should invalidate them.

Run a dry run before the first real upload. The first deployment transfers roughly 6.1 GB; subsequent deployments should transfer only changed objects and metadata.

## Files

- `vf_templates.py` — new shared base, index, category, discussion, and profile templates
- `vf_static.py` — new deterministic static generator and link resolver
- `vf_viewer.py` — imports shared templates and binds the Flask URL helpers; rendered behavior remains unchanged
- `Justfile` — retain `sync` and add static `build`, targeted `preview`, and cache-aware `deploy` recipes
- `.gitignore` — add `_site/`, `_preview/`, and generator staging directories

## Verification

1. Run `uv run python vf_static.py --db vanilla.db --output _site` and verify approximately 4,702 HTML pages, with the exact count derived from the database.
2. Run the `preview` recipe and confirm it emits only the selected discussions, contains symlinked assets and `THIS_IS_A_PREVIEW`, and renders the maintained short, image-heavy, and paginated test cases correctly.
3. Confirm that `deploy` refuses the preview output.
4. Prove full-build determinism by building twice from the same inputs and comparing file manifests and hashes.
5. Serve `_site` locally and compare it against `just view`: index, category, profile, a thread's first and second pages, the 357-page thread's last page, a comment permalink target, a dated `#latest` link, local and failed-remote images, and missing-avatar fallback behavior.
6. Run a local crawler over every generated full-build HTML file. Fail on a missing local `href` or `src`, a missing comment fragment target, Flask-only URLs, unresolved Jinja expressions, or unsafe path traversal.
7. Assert that canonical HTML contains no query-string pagination, `/image/{id}`, `/avatar/{id}`, `/discussion/comment/{id}`, Flask `url_for`, or legacy extensionless navigation links.
8. Run the scoped S3 sync commands with `--dryrun`; confirm that HTML and asset cache policies differ and that deletions are limited to their intended scopes.
9. After deployment, verify canonical deep links, response `Content-Type` and `Cache-Control` headers, and a CloudFront HTML invalidation. Confirm separately that the EC2 `sync` recipe still works.

## Alternatives considered

**Hugo or another SSG framework.** Rejected because the content lives in SQLite as pre-rendered HTML, not Markdown. An exporter would still need to walk the same tables, reproduce dated historical-link resolution, map comment IDs to pages, rewrite images, and then port the existing Jinja templates and CSS. Hugo's Markdown pipeline would be bypassed, and its general paginator does not naturally match the rule that the original post appears only on page 1 and is excluded from the comment count.

A framework would become more attractive if the archive later adds tag indexes, author indexes beyond the existing profiles, feeds, theme variants, or other features that justify its content model.

**Full-text search.** Not included here. The archive currently has no FTS5 table or search route. Pagefind could be added later as a post-build step over the generated HTML without changing the generator's database model.

## Notes

- The current templates load Google Fonts remotely. Vendor the stylesheet and WOFF files if long-term offline preservation is a goal.
- Roughly 126 downloaded image rows have an HTML content type and appear to be saved error pages. They must not be targets of rewritten `<img>` URLs and may be omitted from the staged assets.
- `content_images` remains unnecessary for rendering. `export_meta.export_timestamp` should supply the footer timestamp.

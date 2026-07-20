# Kissena Forum Archive

Tools for exporting the Kissena Cycling Club Vanilla forum to SQLite, browsing the export locally, and publishing it as a static archive on S3 and CloudFront.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- [`just`](https://just.systems/)
- AWS CLI for deployment

## Common commands

```sh
just export              # update vanilla.db using token.txt
just view                # run the local Flask viewer on port 5001
just preview             # build selected discussions into _preview
just build               # build and validate the complete site in _site
just deploy-dry-run       # show the proposed S3 changes
just deploy               # publish _site and invalidate CloudFront HTML
```

Shared defaults—including the database, output directories, public hostname, bucket, and CloudFront distribution—are defined at the top of the `Justfile`. Recipe arguments can override them.

The static output symlinks `images/` and `avatars/` rather than copying them. Image URLs recorded in SQLite are generated even when a build machine has only a partial image tree, so ensure the complete asset archive is in S3 before publishing the HTML.

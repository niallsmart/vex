archive_db := "vanilla.db"
site_output := "_site"
preview_output := "_preview"
archive_hostname := "forum-archive.kissenacycling.com"
archive_bucket := archive_hostname
cloudfront_distribution_id := "E2GNAD70XLW8QR"

default:
    echo "Hello, World!"

export:
    uv run python vf_export.py --url https://kissenacycling.vanillacommunity.com/ --token-file ./token.txt --output "{{archive_db}}" --trace-api --rate-limit 10

view db=archive_db port="5001":
    uv run python vf_viewer.py --db {{db}} --port {{port}}

sync:
    rsync -avcz --exclude-from rsync-exclude.txt . ec2-user@13.223.80.160:vex

sync-from host remote_dir:
    rsync -avcz --info=progress2 {{host}}:{{remote_dir}}/images/ images/
    rsync -avcz --info=progress2 {{host}}:{{remote_dir}}/avatars/ avatars/

build db=archive_db output=site_output hostname=archive_hostname:
    uv run python vf_static.py --db "{{db}}" --output "{{output}}" --base-url "https://{{hostname}}"

preview db=archive_db output=preview_output hostname=archive_hostname:
    uv run python vf_static.py --db "{{db}}" --output "{{output}}" --base-url "https://{{hostname}}" --preview --discussion 382 --discussion 1325 --discussion 18

deploy bucket=archive_bucket distribution_id=cloudfront_distribution_id db=archive_db output=site_output:
    test ! -e "{{output}}/THIS_IS_A_PREVIEW"
    # aws s3 sync "{{output}}/images/" "s3://{{bucket}}/images/" --delete --follow-symlinks --cache-control "public,max-age=31536000,immutable"
    aws s3 sync "{{output}}/avatars/" "s3://{{bucket}}/avatars/" --delete --follow-symlinks --cache-control "public,max-age=86400"
    aws s3 sync "{{output}}/" "s3://{{bucket}}/" --delete --exclude "*" --include "*.html" --cache-control "public,max-age=300" --content-type "text/html"
    aws s3 cp "{{output}}/avatar-placeholder.svg" "s3://{{bucket}}/avatar-placeholder.svg" --cache-control "public,max-age=86400" --content-type "image/svg+xml"
    @if [ -n "{{distribution_id}}" ]; then aws cloudfront create-invalidation --distribution-id "{{distribution_id}}" --paths "/index.html" "/category/*" "/discussion/*" "/profile/*"; fi

deploy-dry-run bucket=archive_bucket db=archive_db output=site_output hostname=archive_hostname:
    test ! -e "{{output}}/THIS_IS_A_PREVIEW"
    just build "{{db}}" "{{output}}" "{{hostname}}"
    # aws s3 sync "{{output}}/images/" "s3://{{bucket}}/images/" --delete --follow-symlinks --cache-control "public,max-age=31536000,immutable" --dryrun
    aws s3 sync "{{output}}/avatars/" "s3://{{bucket}}/avatars/" --delete --follow-symlinks --cache-control "public,max-age=86400" --dryrun
    aws s3 sync "{{output}}/" "s3://{{bucket}}/" --delete --exclude "*" --include "*.html" --cache-control "public,max-age=300" --content-type "text/html" --dryrun
    aws s3 cp "{{output}}/avatar-placeholder.svg" "s3://{{bucket}}/avatar-placeholder.svg" --cache-control "public,max-age=86400" --content-type "image/svg+xml" --dryrun

#!/bin/zsh

set -euo pipefail

archive_bucket="forum-archive.kissenacycling.com"
archive_region="us-east-1"

print "Creating S3 bucket: ${archive_bucket} (${archive_region})"

aws s3api create-bucket \
  --bucket "${archive_bucket}" \
  --region "${archive_region}" \
  --object-ownership BucketOwnerEnforced

aws s3api put-public-access-block \
  --bucket "${archive_bucket}" \
  --region "${archive_region}" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

print "Configured private archive bucket: ${archive_bucket}"

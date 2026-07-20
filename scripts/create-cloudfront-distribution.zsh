#!/bin/zsh

set -euo pipefail

archive_bucket="forum-archive.kissenacycling.com"
archive_region="us-east-1"
origin_id="kissena-forum-archive-s3"
oac_name="kissena-forum-archive-oac"
cache_policy_id="658327ea-f89d-4fab-a63d-7e88639e58f6"
caller_reference="kissena-forum-archive-$(date -u +%Y%m%dT%H%M%SZ)"
origin_domain="${archive_bucket}.s3.${archive_region}.amazonaws.com"

print "Checking S3 origin: ${archive_bucket}"
aws s3api head-bucket --bucket "${archive_bucket}" --region "${archive_region}"

print "Creating CloudFront origin access control"
oac_id="$(
  aws cloudfront create-origin-access-control \
    --origin-access-control-config \
      "Name=${oac_name},Description=Read-only access to the Kissena forum archive,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
    --query 'OriginAccessControl.Id' \
    --output text
)"

distribution_config="$(cat <<JSON
{
  "CallerReference": "${caller_reference}",
  "Aliases": {
    "Quantity": 0
  },
  "DefaultRootObject": "index.html",
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "${origin_id}",
        "DomainName": "${origin_domain}",
        "OriginPath": "",
        "CustomHeaders": {
          "Quantity": 0
        },
        "S3OriginConfig": {
          "OriginAccessIdentity": ""
        },
        "ConnectionAttempts": 3,
        "ConnectionTimeout": 10,
        "OriginAccessControlId": "${oac_id}",
        "OriginShield": {
          "Enabled": false
        }
      }
    ]
  },
  "OriginGroups": {
    "Quantity": 0
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "${origin_id}",
    "TrustedSigners": {
      "Enabled": false,
      "Quantity": 0
    },
    "TrustedKeyGroups": {
      "Enabled": false,
      "Quantity": 0
    },
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 2,
      "Items": ["GET", "HEAD"],
      "CachedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"]
      }
    },
    "SmoothStreaming": false,
    "Compress": true,
    "LambdaFunctionAssociations": {
      "Quantity": 0
    },
    "FunctionAssociations": {
      "Quantity": 0
    },
    "FieldLevelEncryptionId": "",
    "CachePolicyId": "${cache_policy_id}"
  },
  "CacheBehaviors": {
    "Quantity": 0
  },
  "CustomErrorResponses": {
    "Quantity": 0
  },
  "Comment": "Kissena forum static archive",
  "Logging": {
    "Enabled": false,
    "IncludeCookies": false,
    "Bucket": "",
    "Prefix": ""
  },
  "PriceClass": "PriceClass_100",
  "Enabled": true,
  "ViewerCertificate": {
    "CloudFrontDefaultCertificate": true,
    "MinimumProtocolVersion": "TLSv1",
    "CertificateSource": "cloudfront"
  },
  "Restrictions": {
    "GeoRestriction": {
      "RestrictionType": "none",
      "Quantity": 0
    }
  },
  "WebACLId": "",
  "HttpVersion": "http2and3",
  "IsIPV6Enabled": true,
  "Staging": false
}
JSON
)"

print "Creating CloudFront distribution"
read -r distribution_id distribution_domain <<< "$(
  aws cloudfront create-distribution \
    --distribution-config "${distribution_config}" \
    --query 'Distribution.[Id,DomainName]' \
    --output text
)"

account_id="$(aws sts get-caller-identity --query Account --output text)"
bucket_policy="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipalReadOnly",
      "Effect": "Allow",
      "Principal": {
        "Service": "cloudfront.amazonaws.com"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${archive_bucket}/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::${account_id}:distribution/${distribution_id}"
        }
      }
    }
  ]
}
JSON
)"

print "Granting the distribution read-only access to the S3 origin"
aws s3api put-bucket-policy \
  --bucket "${archive_bucket}" \
  --region "${archive_region}" \
  --policy "${bucket_policy}"

print "Waiting for CloudFront deployment to complete"
aws cloudfront wait distribution-deployed --id "${distribution_id}"

print "CloudFront distribution created"
print "Distribution ID: ${distribution_id}"
print "Distribution URL: https://${distribution_domain}"
print "Deploy with: just deploy ${archive_bucket} ${distribution_id}"

#!/usr/bin/env bash
# Applies or shows the backup bucket's Object Lock default retention and lifecycle rules
# from infra/backup/bucket/. The bucket itself is created by hand in the Hetzner console
# with Object Lock enabled: Hetzner allows the lock only at creation, and versioning comes
# with it. Credentials come from the caller's environment and never touch a file.
#
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... scripts/ops/backup_bucket.sh apply <bucket> <region>
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... scripts/ops/backup_bucket.sh show <bucket> <region>
#
# <region> is the Hetzner location (fsn1, nbg1, hel1); the endpoint is built from it.
set -euo pipefail

AWS_CLI_IMAGE="amazon/aws-cli:2.38.1"
config_dir="$(cd "$(dirname "$0")/../../infra/backup/bucket" && pwd)"

usage() {
    echo "Usage: $0 apply|show <bucket> <region>" >&2
    exit 2
}

[ "$#" -eq 3 ] || usage
command="$1"
bucket="$2"
region="$3"
case "$region" in
    fsn1 | nbg1 | hel1) ;;
    *) usage ;;
esac
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"

aws() {
    # The key values pass by name only, so they never appear on a command line.
    docker run --rm \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY \
        -e AWS_DEFAULT_REGION="$region" \
        -v "$config_dir:/config:ro" \
        "$AWS_CLI_IMAGE" \
        --endpoint-url "https://$region.your-objectstorage.com" \
        s3api "$@"
}

case "$command" in
    apply)
        aws put-object-lock-configuration --bucket "$bucket" \
            --object-lock-configuration file:///config/object-lock.json
        aws put-bucket-lifecycle-configuration --bucket "$bucket" \
            --lifecycle-configuration file:///config/lifecycle.json
        echo "Applied object lock and lifecycle to $bucket"
        ;;
    show)
        echo "versioning:"
        aws get-bucket-versioning --bucket "$bucket"
        echo "object lock:"
        aws get-object-lock-configuration --bucket "$bucket"
        echo "lifecycle:"
        # aws-cli crashes on Hetzner's answer for a bucket that has no lifecycle yet.
        if ! aws get-bucket-lifecycle-configuration --bucket "$bucket"; then
            echo "lifecycle: none or unreadable (a bucket without lifecycle fails this way; run apply)" >&2
            exit 1
        fi
        ;;
    *)
        usage
        ;;
esac

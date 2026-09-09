"""Install the scoped one-day fallback without replacing unrelated bucket rules."""

import argparse
import hashlib

import boto3
from botocore.exceptions import ClientError


def configure(s3, bucket, prefix):
    prefix = prefix.strip("/") + "/"
    if prefix == "/":
        raise ValueError("A nonempty prefix is required; never expire the whole bucket")
    rule_id = "case2audio-expire-1-day-" + hashlib.sha256(prefix.encode()).hexdigest()[:12]
    rule = {
        "ID": rule_id,
        "Filter": {"Prefix": prefix},
        "Status": "Enabled",
        "Expiration": {"Days": 1},
        # Future bucket versioning must not leave old audio versions billed indefinitely.
        "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }
    try:
        existing = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
            raise
        existing = {"Rules": []}
    previous = existing.get("Rules", [])
    rules = [entry for entry in previous if entry.get("ID") != rule_id] + [rule]
    args = {"Bucket": bucket, "LifecycleConfiguration": {"Rules": rules}}
    # Preserve the bucket's transition-size setting even though our rule only expires objects.
    if "TransitionDefaultMinimumObjectSize" in existing:
        args["TransitionDefaultMinimumObjectSize"] = existing["TransitionDefaultMinimumObjectSize"]
    s3.put_bucket_lifecycle_configuration(**args)
    actual = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    if rule not in actual or any(r not in actual for r in previous if r.get("ID") != rule_id):
        raise RuntimeError("Lifecycle read-back did not match the requested rules")
    print(f"Verified one-day expiry for s3://{bucket}/{prefix}; unrelated rules preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default="polly-gsk")
    parser.add_argument("--prefix", default="case2audio")
    parser.add_argument("--profile", default="case2audio")
    args = parser.parse_args()
    configure(boto3.Session(profile_name=args.profile).client("s3"), args.bucket, args.prefix)

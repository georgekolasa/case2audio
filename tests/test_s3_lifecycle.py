import runpy
from copy import deepcopy
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

configure = runpy.run_path(str(Path(__file__).parents[1] / "scripts/set_s3_lifecycle.py"))[
    "configure"
]


class FakeS3:
    def __init__(self, rules=None):
        self.rules = rules
        self.writes = []

    def get_bucket_lifecycle_configuration(self, **kwargs):
        if self.rules is None:
            raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetLifecycle")
        return {
            "Rules": deepcopy(self.rules),
            "TransitionDefaultMinimumObjectSize": "all_storage_classes_128K",
        }

    def put_bucket_lifecycle_configuration(self, **kwargs):
        self.writes.append(kwargs)
        self.rules = deepcopy(kwargs["LifecycleConfiguration"]["Rules"])


def test_scoped_expiration_preserves_other_rules_and_is_repeatable():
    unrelated = {
        "ID": "backups",
        "Filter": {"Prefix": "backups/"},
        "Status": "Enabled",
        "Expiration": {"Days": 90},
    }
    s3 = FakeS3([unrelated])
    configure(s3, "bucket", "case2audio")
    configure(s3, "bucket", "case2audio/")
    assert len(s3.rules) == 2
    assert s3.rules[0] == unrelated
    assert s3.rules[1]["Filter"] == {"Prefix": "case2audio/"}
    assert s3.rules[1]["Expiration"] == {"Days": 1}
    assert s3.writes[0]["TransitionDefaultMinimumObjectSize"] == "all_storage_classes_128K"


def test_new_rule_and_empty_prefix_guard():
    s3 = FakeS3()
    configure(s3, "bucket", "case2audio")
    assert len(s3.rules) == 1
    with pytest.raises(ValueError, match="nonempty"):
        configure(s3, "bucket", "")
    assert len(s3.writes) == 1

import pytest

from case2audio.cleaner import clean_markdown


def test_columbia_notice_is_removed_without_losing_merged_body_text():
    markdown = (
        "# Copyright information\n\n"
        "© 2008 - 2024 by The Trustees of Columbia University in the City of New York. "
        "This case includes minor editorial changes and data updates made to the version "
        "originally published on July 28, 2008.\n\n"
        "This case is for teaching purposes only and does not represent an endorsement "
        "or judgment of the material included.\n\n"
        "This case cannot be used or reproduced without explicit permission from Columbia "
        "CaseWorks. To obtain permission, please visit caseworks.business.columbia.edu, "
        "or e-mail ColumbiaCaseWorks@gsb.columbia.edu This pattern is not unique to firms "
        "in the United States. The data for South Korea reveal the same pattern.\n"
    )
    assert clean_markdown(markdown) == (
        "This pattern is not unique to firms in the United States. "
        "The data for South Korea reveal the same pattern.\n"
    )


@pytest.mark.parametrize("prefix", ["©", "(c)", "Copyright", "Copyright ©", "Copyright (C)"])
def test_copyright_variants_preserve_following_factual_sentence(prefix):
    text = f"{prefix} 2015–2026 by Example University. All rights reserved. Revenue rose 20%."
    assert clean_markdown(text) == "Revenue rose 20%.\n"


def test_wrapped_notice_and_separate_rights_line_are_removed():
    text = """© 2008 - 2024 by The Trustees of Columbia University
in the City of New York.

All rights reserved.

This case cannot be used or reproduced without explicit permission from Columbia CaseWorks.
To obtain permission, please visit caseworks.business.columbia.edu,
or e-mail ColumbiaCaseWorks@gsb.columbia.edu . Case prose follows.
"""
    assert clean_markdown(text) == "Case prose follows.\n"


def test_ordinary_discussion_of_copyright_is_unchanged():
    text = """# Copyright

Copyright protection affects the company's licensing revenue. The firm obtained permission
to reproduce the design. A teaching license was part of its strategy.
"""
    assert clean_markdown(text) == (
        "Copyright\n\nCopyright protection affects the company's licensing revenue. "
        "The firm obtained permission to reproduce the design. "
        "A teaching license was part of its strategy.\n"
    )


def test_heading_does_not_swallow_unknown_following_content():
    assert clean_markdown("# Copyright information\n\nThe company sold 60 licenses.") == (
        "The company sold 60 licenses.\n"
    )


def test_ocr_publishing_notices_leave_adjacent_case_prose():
    text = (
        "This version of the case replaces an earlier version that was published in 2013. "
        "This case is used as a teaching tool and not for research and is purely fictional. "
        "Cases may contain fictionalized elements. The budget is €75 million."
    )
    assert clean_markdown(text) == "The budget is €75 million.\n"

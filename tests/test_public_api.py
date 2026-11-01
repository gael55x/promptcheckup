import promptcheckup


def test_top_level_exports_only_the_user_facing_api():
    # Every top-level name is a promise to users; internals stay in their subpackages.
    assert set(promptcheckup.__all__) == {"Verdict", "decide", "record"}

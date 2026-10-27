from vertext_ai_proxy.models import normalize_model_name


def test_normalize_model_name_auto_prefixes():
    assert normalize_model_name("gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert normalize_model_name("gemini-1.5-pro") == "google/gemini-1.5-pro"
    assert normalize_model_name("learnlm-1.5-pro") == "google/learnlm-1.5-pro"


def test_normalize_model_name_preserves_existing_prefix():
    assert normalize_model_name("google/gemini-2.5-flash") == "google/gemini-2.5-flash"
    assert normalize_model_name("meta/llama-3-70b") == "meta/llama-3-70b"


def test_normalize_model_name_respects_flag():
    assert (
        normalize_model_name("gemini-2.5-flash", auto_prefix=False)
        == "gemini-2.5-flash"
    )

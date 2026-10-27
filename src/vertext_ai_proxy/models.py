def normalize_model_name(model_name: str, auto_prefix: bool = True) -> str:
    """如果客户端传入的模型未带 'google/' 等供应商前缀，自动为其补全前缀。"""
    if not auto_prefix or not model_name:
        return model_name

    stripped = model_name.strip()
    # 如果已经包含供应商前缀（如 google/ 或 meta/），直接返回
    if "/" in stripped:
        return stripped

    # 如果是常见的 Google Gemini 系列模型命名，补全 google/
    if (
        stripped.startswith("gemini-")
        or stripped.startswith("learnlm-")
        or stripped.startswith("imagen-")
    ):
        return f"google/{stripped}"

    return stripped

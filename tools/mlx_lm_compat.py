"""Compatibility shims for mlx-lm in this local navspec workflow."""

from __future__ import annotations


def patch_transformers_5_tokenizer_register() -> None:
    """Allow mlx-lm 0.31.x to import with transformers 5.x.

    mlx-lm registers its NewlineTokenizer with a string key. transformers 5.x
    expects a config class there and raises AttributeError. Qwen does not need
    that tokenizer, so ignoring only string-key registrations is sufficient for
    this workflow and keeps the installed package untouched.
    """

    try:
        from transformers.models.auto.tokenization_auto import AutoTokenizer
    except Exception:
        return

    if getattr(AutoTokenizer.register, "_navspec_compat_patched", False):
        return

    original_register = AutoTokenizer.register

    def register(config_class, slow_tokenizer_class=None, fast_tokenizer_class=None, exist_ok=False):
        if isinstance(config_class, str):
            return None
        return original_register(
            config_class,
            slow_tokenizer_class=slow_tokenizer_class,
            fast_tokenizer_class=fast_tokenizer_class,
            exist_ok=exist_ok,
        )

    register._navspec_compat_patched = True  # type: ignore[attr-defined]
    AutoTokenizer.register = staticmethod(register)

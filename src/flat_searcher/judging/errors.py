"""Errors the judge reacts to differently."""


class PromptTooLargeError(RuntimeError):
    """The model's context cannot hold the prompt; the caller may retry with less."""

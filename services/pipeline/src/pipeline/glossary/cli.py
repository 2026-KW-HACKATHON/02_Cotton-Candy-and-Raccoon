"""Default entry point for Gemini notice conversion."""

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Gemini notice conversion command."""
    from pipeline.glossary.easy_language_cli import main as easy_language_main

    return easy_language_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

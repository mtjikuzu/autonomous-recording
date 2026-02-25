"""
Gamma API Client for generating and caching presentation slides.

This module handles:
1. Generating presentations via Gamma API
2. Exporting to PNG images
3. Caching to avoid regeneration
4. Managing API keys and rate limits
"""

import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional
import requests


GAMMA_API_BASE = "https://public-api.gamma.app"


class GammaError(Exception):
    """Raised when Gamma API calls fail."""

    pass


class GammaClient:
    """Client for Gamma API to generate presentations."""

    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        """
        Initialize Gamma client.

        Args:
            api_key: Gamma API key. If None, reads from GAMMA_API_KEY env var.
            cache_dir: Directory to cache generated slides. Defaults to ~/.cache/gamma-slides
        """
        self.api_key = api_key or os.environ.get("GAMMA_API_KEY")
        if not self.api_key:
            raise GammaError(
                "Gamma API key required. Set GAMMA_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self.cache_dir = cache_dir or Path.home() / ".cache" / "gamma-slides"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        )

    def _content_hash(self, content: dict) -> str:
        """Generate a hash for content to use as cache key."""
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()[:16]

    def _get_cached_path(self, content_hash: str) -> Optional[Path]:
        """Check if slides are already cached."""
        cached = self.cache_dir / content_hash
        if cached.exists() and any(cached.iterdir()):
            return cached
        return None

    def generate_presentation(
        self,
        title: str,
        content: list[dict],
        theme: str = "Chisel",
        language: str = "en",
    ) -> Path:
        """
        Generate a presentation and export as PNG images.

        Args:
            title: Presentation title
            content: List of slide content dictionaries
            theme: Gamma theme name (default: "Chisel")
            language: Language code (default: "en")

        Returns:
            Path to directory containing PNG slides (slide-001.png, slide-002.png, etc.)

        The result is cached based on content hash. Subsequent calls with identical
        content will return cached slides without API call.
        """
        # Build content for hashing
        content_data = {
            "title": title,
            "content": content,
            "theme": theme,
            "language": language,
        }
        content_hash = self._content_hash(content_data)

        # Check cache
        cached = self._get_cached_path(content_hash)
        if cached:
            print(f"[Gamma] Using cached slides: {cached}")
            return cached

        # Generate via API
        print(f"[Gamma] Generating presentation: {title}")

        # Convert content to Gamma's expected format
        input_text = self._build_input_text(title, content)

        payload = {
            "inputText": input_text,
            "textMode": "generate",
            "format": "presentation",
            "themeId": theme,
            "textOptions": {"language": language},
        }

        try:
            response = self.session.post(
                f"{GAMMA_API_BASE}/v1.0/generations", json=payload, timeout=120
            )
            response.raise_for_status()
            result = response.json()

            gamma_id = result.get("gammaId")
            if not gamma_id:
                raise GammaError(f"No gammaId in response: {result}")

            print(f"[Gamma] Created presentation: {gamma_id}")

            # Wait for generation to complete
            slides_dir = self._wait_and_export(gamma_id, content_hash)
            return slides_dir

        except requests.RequestException as e:
            raise GammaError(f"Gamma API request failed: {e}")

    def _build_input_text(self, title: str, content: list[dict]) -> str:
        """Build input text for Gamma API from structured content."""
        lines = [f"# {title}", ""]

        for slide in content:
            slide_type = slide.get("type", "content")

            if slide_type == "title":
                lines.append(f"## {slide.get('title', '')}")
                if "subtitle" in slide:
                    lines.append(slide["subtitle"])

            elif slide_type == "content":
                lines.append(f"## {slide.get('title', '')}")
                if "bullet_points" in slide:
                    for point in slide["bullet_points"]:
                        lines.append(f"- {point}")
                if "text" in slide:
                    lines.append(slide["text"])

            elif slide_type == "code":
                lines.append(f"## {slide.get('title', '')}")
                lines.append("```java")
                lines.append(slide.get("code", ""))
                lines.append("```")

            lines.append("")  # Empty line between slides

        return "\n".join(lines)

    def _wait_and_export(self, gamma_id: str, content_hash: str) -> Path:
        """
        Wait for generation to complete and export as PNGs.

        Note: Gamma API doesn't have a direct "export to PNG" endpoint.
        We need to poll for completion, then use the web interface or
        a headless browser to export. For now, this is a placeholder
        that would need to be implemented based on actual Gamma API capabilities.
        """
        # TODO: Implement actual export once Gamma API export endpoints are confirmed
        # For now, create placeholder directory
        slides_dir = self.cache_dir / content_hash
        slides_dir.mkdir(exist_ok=True)

        # Placeholder: In real implementation, this would:
        # 1. Poll gamma.app for completion status
        # 2. Use export endpoint or headless browser to download PNGs
        # 3. Save to slides_dir as slide-001.png, slide-002.png, etc.

        print(f"[Gamma] Placeholder: slides would be saved to {slides_dir}")
        print(f"[Gamma] In production, implement actual export via Gamma web interface")

        return slides_dir


def get_gamma_client() -> GammaClient:
    """Get configured Gamma client."""
    return GammaClient()


if __name__ == "__main__":
    # Test the client
    try:
        client = get_gamma_client()

        # Example content for bubble sort theory slides
        content = [
            {
                "type": "title",
                "title": "Understanding Bubble Sort",
                "subtitle": "A gentle introduction to the classic sorting algorithm",
            },
            {
                "type": "content",
                "title": "What is Bubble Sort?",
                "bullet_points": [
                    "Simple comparison-based sorting algorithm",
                    "Repeatedly steps through the list",
                    "Compares adjacent elements and swaps them if needed",
                    "Named for the way smaller elements 'bubble' to the top",
                ],
            },
            {
                "type": "content",
                "title": "How It Works",
                "bullet_points": [
                    "Start from the beginning of the array",
                    "Compare each pair of adjacent elements",
                    "Swap them if they are in the wrong order",
                    "Repeat until no more swaps are needed",
                ],
            },
        ]

        result = client.generate_presentation(
            title="Bubble Sort Theory", content=content
        )
        print(f"Slides saved to: {result}")

    except GammaError as e:
        print(f"Error: {e}")

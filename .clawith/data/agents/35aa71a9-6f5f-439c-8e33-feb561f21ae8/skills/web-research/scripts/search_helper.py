#!/usr/bin/env python3
"""Helper utilities for structured web search."""

from datetime import datetime


def format_search_results(results: list[dict]) -> str:
    """Format raw search results into a structured report."""
    output = []
    for i, r in enumerate(results, 1):
        title = r.get('title', 'Untitled')
        url = r.get('url', '#')
        snippet = r.get('snippet', 'No description')
        output.append(f'{i}. [{title}]({url})')
        output.append(f'   {snippet}')
        output.append('')
    return '\n'.join(output)


def assess_source_credibility(url: str) -> dict:
    """Basic heuristics for source credibility."""
    trusted = ['.edu', '.gov', '.org', 'arxiv.org', 'nature.com']
    score = 0.5
    for d in trusted:
        if d in url:
            score = 0.8
            break
    return {'url': url, 'credibility_score': score,
            'assessed_at': datetime.now().isoformat()}

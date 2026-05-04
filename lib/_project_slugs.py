"""
Canonical project slug taxonomy + path-to-slug resolver.

Every write into knowledge.db (lessons, chunks, briefs) carries a project slug.
Slugs are the cross-reference key for the project_context_card manifest. Bad slugs
break briefs silently — so we validate at write time.

This file is the SINGLE source of truth for valid slugs in your install.

WHEN YOU SET UP RunawayContext:
    1. Edit CANONICAL_PROJECT_SLUGS below — list every project the system should
       know about, in lowercase snake_case.
    2. Edit PATH_TO_SLUG to map filesystem paths (project directories) to slugs,
       so the auto-mining + migration scripts can derive slugs from file paths.
    3. Re-run any propose / brief / mining commands — they'll pick up the new list.

If you add a project later: append to CANONICAL_PROJECT_SLUGS + PATH_TO_SLUG.
The validate_project_arg helper will reject typos at write time.
"""
import os
import re

# === EDIT ME — list every canonical project slug for your install ===
# Lowercase, snake_case, no path components. Keep alphabetized.
CANONICAL_PROJECT_SLUGS = {
    'general',
    # === EXAMPLES — replace with your own ===
    # 'frontend', 'api', 'mobile', 'data_pipeline', 'ml_training',
    # 'admin_tool', 'reporting', 'integrations',
}

# === EDIT ME — map directory paths to slugs (longest-match wins) ===
# Keys are paths relative to your repo root or absolute paths if you cross repos.
# The slug_from_path() resolver tries the longest matching prefix first.
PATH_TO_SLUG = {
    # === EXAMPLES — replace with your own ===
    # 'apps/frontend':                'frontend',
    # 'apps/api':                     'api',
    # 'apps/mobile':                  'mobile',
    # 'pipelines/etl':                'data_pipeline',
    # 'ml/training':                  'ml_training',
    # 'tools/admin':                  'admin_tool',
    # 'reports':                      'reporting',
    # 'integrations/stripe':          'integrations',
    # 'integrations/slack':           'integrations',
}

# Optional: directories the slug resolver should NEVER tag (returned as None).
# These are typically vendor / build / backup paths that shouldn't pollute the KS.
JUNK_PATH_MARKERS = (
    'node_modules', 'vendor/', '.bak', '.backup.',
    '/backups/', '/_archive/', '/dist/', '/build/',
    '__pycache__', '.cache/',
)


def slug_from_path(path):
    """Resolve a file path → canonical project slug.

    Returns:
        - 'general' if no PATH_TO_SLUG match
        - None if the path matches a junk marker (caller should skip)
        - the matched slug otherwise
    """
    if not path:
        return 'general'
    if any(x in path for x in JUNK_PATH_MARKERS):
        return None

    rel = path
    # Strip filename — match against directories
    if '/' in rel and ('.' in os.path.basename(rel)):
        rel = os.path.dirname(rel)

    # Try longest prefix first so the most specific mapping wins
    for prefix in sorted(PATH_TO_SLUG.keys(), key=len, reverse=True):
        if rel == prefix or rel.endswith('/' + prefix) or ('/' + prefix + '/') in rel or rel.startswith(prefix + '/') or rel == prefix:
            return PATH_TO_SLUG[prefix]

    return 'general'


def normalize_user_input_slugs(value):
    """Validate and normalize a comma-separated --project string.
    Returns list of canonical slugs or raises ValueError.

    Used by argparse validators in the CLI.
    """
    if value is None or value == '':
        raise ValueError(
            "project slug required. Use a slug from your install's "
            "CANONICAL_PROJECT_SLUGS in lib/_project_slugs.py"
        )
    slugs = [s.strip().lower() for s in re.split(r'[,\s]+', value) if s.strip()]
    if not slugs:
        raise ValueError("project slug required")
    unknown = [s for s in slugs if s not in CANONICAL_PROJECT_SLUGS]
    if unknown:
        raise ValueError(
            f"Unknown project slug(s): {unknown}.\n"
            f"Known slugs in this install: {sorted(CANONICAL_PROJECT_SLUGS)}.\n"
            f"To add a new slug: append it to CANONICAL_PROJECT_SLUGS in "
            f"lib/_project_slugs.py."
        )
    seen, out = set(), []
    for s in slugs:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out

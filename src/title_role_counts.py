"""Title role counts: gives every posting a standard role and a level, and reports coverage.

Run from the repo root, after build_title_roles.py:

    python src/title_role_counts.py
    python src/title_role_counts.py --postings "data/linkedin_job_postings_2023-2024.csv"

    If the first option doesnt work, use the 2nd command.

Role: looked up in data/title_roles.csv by the cleaned title.

Level, first match wins:
1. The experience level column (LinkedIn's six levels, mapped in LINKEDIN_LEVELS):
   Internship and Entry level are Entry (Jr.), Associate is Mid (no prefix),
   Director and Executive are Senior (Sr.). Mid-Senior level is both, so it is
   Senior when the title has a senior word or the description asks for 6+ years,
   and Mid otherwise.
2. Level words in the title ("Sr.", "Junior", "Lead", ...).
3. Years in the description: 0-2 Entry, 3-5 Mid, 6+ Senior. The first number that
   looks like a requirement counts. "18 years of age" and numbers over 30 ("over 95
   years of experience" about the company) are skipped.
4. Otherwise the level stays blank.

Writes data/processed/postings_with_roles.csv (every column plus standard_role,
level and standard_title), reports/title_role_counts.md and reports/unmapped_titles.csv
(most common unmapped titles with an empty role column to fill in and paste into
data/reference/title_overrides.csv).
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

from build_title_roles import DEFAULT_MAPPING, title_key

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSTINGS = REPO_ROOT / "data" / "processed" / "postings_clean.csv"
DEFAULT_OUT_FILE = REPO_ROOT / "data" / "processed" / "postings_with_roles.csv"
DEFAULT_REPORTS = REPO_ROOT / "reports"
TARGET = 0.9

# edit these to change the rules
LINKEDIN_LEVELS = {
    "internship": "Entry",
    "entry level": "Entry",
    "associate": "Mid",
    "mid-senior level": "Mid-Senior",
    "director": "Senior",
    "executive": "Senior",
}
MAX_YEARS = 30  # a bigger number is the company's age, not a requirement
YEARS = re.compile(r"(\d+)\s*(?:(?:-|–|to)\s*\d+\s*)?\+?\s*(?:years?|yrs?)\b(?!\s*(?:of age|old))", re.IGNORECASE)
TITLE_SENIOR_WORDS = {"senior", "sr", "lead", "principal"}
TITLE_ENTRY_WORDS = {"junior", "jr", "entry", "intern", "internship", "trainee"}

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# level -> (prefix, first year count that belongs to it)
DEFAULT_LEVELS = {"Entry": ("Jr.", 0), "Mid": ("", 3), "Senior": ("Sr.", 6)}


class ExperienceLevels:
    def __init__(self, levels=None):
        self.prefixes, self.min_years = {}, {}
        for level, (prefix, min_years) in (levels or DEFAULT_LEVELS).items():
            self.prefixes[level] = prefix
            self.min_years[level] = min_years

    def level_for(self, years):
        # the highest level whose start is at or below this many years
        return max((lv for lv in self.min_years if self.min_years[lv] <= years), key=self.min_years.get)

    def title_for(self, role, level):
        # "Jr. Data Scientist", "Data Scientist" or "Sr. Data Scientist"
        if not role or not level:
            return None
        return " ".join(f"{self.prefixes[level]} {role}".split())

def level_from_title(title):
    words = re.sub(r"[^a-z0-9 ]", " ", str(title or "").lower()).split()
    if TITLE_SENIOR_WORDS & set(words):
        return "Senior"
    if TITLE_ENTRY_WORDS & set(words):
        return "Entry"
    return None


def level_from_years(description, lv):
    # level for the first plausible "N years" in the description, or None
    for m in YEARS.finditer(str(description or "")):
        years = int(m.group(1))
        if years <= MAX_YEARS:
            return lv.level_for(years)
    return None


def resolve_level(label, title, description, lv):
    # returns (level, where it came from)
    mapped = LINKEDIN_LEVELS.get(str(label or "").strip().lower())
    if mapped == "Mid-Senior":
        senior = level_from_title(title) == "Senior" or level_from_years(description, lv) == "Senior"
        return ("Senior" if senior else "Mid"), "column"
    if mapped:
        return mapped, "column"
    level = level_from_title(title)
    if level:
        return level, "title"
    level = level_from_years(description, lv)
    if level:
        return level, "description"
    return None, "unknown"


def load_mapping(path):
    # returns {cleaned title: role}, only the titles that have a role
    with open(path, encoding="utf-8", newline="") as f:
        return {row["title"]: row["role"] for row in csv.DictReader(f) if row["role"]}


def process(postings_path, mapping, lv, out_path, title_col, level_col, desc_col):
    # one pass over the postings: writes the output file, returns the counts for the report
    stats = {"total": 0, "mapped": 0, "roles": Counter(), "levels": Counter(), "sources": Counter(), "unmapped": Counter(), "example": {}}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(postings_path, encoding="utf-8", newline="") as src, out_path.open("w", encoding="utf-8", newline="") as dst:
        reader = csv.DictReader(src)
        fields = reader.fieldnames or []
        if title_col not in fields:
            raise SystemExit(f"column '{title_col}' not found. Columns are: {', '.join(fields)}. Use --title-column.")
        for name, option in [(level_col, "--level-column"), (desc_col, "--description-column")]:
            if name not in fields:
                print(f"warning: column '{name}' not found, treated as empty ({option})", file=sys.stderr)
        writer = csv.DictWriter(dst, fieldnames=fields + ["standard_role", "level", "standard_title"])
        writer.writeheader()
        for row in reader:
            title = row.get(title_col) or ""
            key = title_key(title)
            role = mapping.get(key, "")
            level, source = resolve_level(row.get(level_col), title, row.get(desc_col), lv)
            row["standard_role"] = role
            row["level"] = level or ""
            row["standard_title"] = lv.title_for(role, level) or ""
            writer.writerow(row)

            stats["total"] += 1
            stats["levels"][level or "Unknown"] += 1
            stats["sources"][source] += 1
            if role:
                stats["mapped"] += 1
                stats["roles"][role] += 1
            else:
                stats["unmapped"][key] += 1
                stats["example"].setdefault(key, title.strip())
    return stats


def write_reports(stats, reports_dir, postings_name):
    reports_dir.mkdir(parents=True, exist_ok=True)
    total, mapped = stats["total"], stats["mapped"]
    share = mapped / total if total else 0
    lines = [
        "# Title role counts",
        "",
        f"Source: `{postings_name}` ({total} postings). Counts are postings.",
        "",
        f"- Standard roles: **{len(stats['roles'])}**",
        f"- Postings with a role: **{mapped} of {total} ({100 * share:.1f}%)**, target of {TARGET:.0%}: **{'met' if share >= TARGET else 'not met'}**",
        f"- Distinct unmapped titles: **{len(stats['unmapped'])}**",
        "",
        "## Level source",
        "",
        "| Source | Postings |",
        "|---|---:|",
    ]
    lines += [f"| {s} | {n} |" for s, n in stats["sources"].most_common()]
    lines += ["", "## Levels", "", "| Level | Postings |", "|---|---:|"]
    lines += [f"| {l} | {n} |" for l, n in stats["levels"].most_common()]
    lines += ["", "## Most common roles", "", "| Role | Postings |", "|---|---:|"]
    lines += [f"| {r} | {n} |" for r, n in stats["roles"].most_common(25)]
    lines += ["", "## Most common unmapped titles", "", "Add these to `data/reference/title_overrides.csv`.", "", "| Title | Postings | Example |", "|---|---:|---|"]
    top = stats["unmapped"].most_common(25)
    lines += [f"| {t or '(blank)'} | {n} | {stats['example'][t]} |" for t, n in top]
    (reports_dir / "title_role_counts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (reports_dir / "unmapped_titles.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "role", "postings"])  # fill in role, then copy the rows to the overrides file
        for t, n in stats["unmapped"].most_common(500):
            if t:
                writer.writerow([t, "", n])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--out-file", type=Path, default=DEFAULT_OUT_FILE)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--title-column", default="title")
    parser.add_argument("--level-column", default="formatted_experience_level")
    parser.add_argument("--description-column", default="description")
    args = parser.parse_args(argv)

    mapping = load_mapping(args.mapping)
    stats = process(args.postings, mapping, ExperienceLevels(), args.out_file, args.title_column, args.level_column, args.description_column)
    print(write_reports(stats, args.reports, args.postings.name))


if __name__ == "__main__":
    main()

"""Build the title-to-role mapping: groups raw job titles into standard roles.

Run from the repo root:
    
python src/build_title_roles.py
python src/build_title_roles.py --postings "data/linkedin_job_postings_2023-2024.csv" --alt-titles "data/reference/onet/onet_job_titles.csv" --occupations "data/reference/onet/occupation_data.csv"

If the first option doesnt work, use the 2nd command.

How a title gets its role (first match wins):
1. data/reference/title_overrides.csv (title,role). The team's file always wins,
   even when O*NET says something different.
2. O*NET: the title matches an occupation title or one of its alternate titles
   ("Alternate Titles.txt" joined to "Occupation Data.txt"). The role is the O*NET
   occupation title.
3. Otherwise the role is blank. These titles show up in the unmapped list.

Titles are compared after cleaning (title_key): lowercase, no punctuation, and no
level words or numerals, so "Sr. Accountant" and "Staff Accountant II" both
become "accountant". A title with separators ("A - B", "A / B") is tried whole
first, then each part, and the first one that matches wins.

Writes data/title_roles.csv (title,role,source,postings), one row per cleaned title,
biggest first. To fix a title, add it to the overrides file and run this again.
Hand edits to title_roles.csv are overwritten on a rebuild.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POSTINGS = REPO_ROOT / "linkedin_job_postings_2023-2024.csv"
DEFAULT_ALT_TITLES = REPO_ROOT / "data" / "reference" / "onet" / "onet_job_titles.csv"
DEFAULT_OCCUPATIONS = REPO_ROOT / "data" / "reference" / "onet" / "occupation_data.csv"
DEFAULT_OVERRIDES = REPO_ROOT / "data" / "reference" / "title_overrides.csv"
DEFAULT_MAPPING = REPO_ROOT / "data" / "title_roles.csv"

# words dropped from a title before matching, they never decide the level
_TITLE_LEVEL_WORDS = {"senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "entry", "level"}
_TITLE_LEVEL_SUFFIXES = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5"}
_TITLE_NOISE = {"remote", "hybrid", "onsite", "contract", "contractor", "temporary", "temp", "fulltime", "parttime"}

def _clean_words(text):
    # words of a title without punctuation, noise words or level words
    text = text.replace("&", " and ")
    text = re.sub(r"[.'’]", "", text)
    text = re.sub(r"[^A-Za-z0-9+# ]", " ", text)
    words = [w for w in text.split() if w.lower() not in _TITLE_NOISE]
    while words and words[0].lower() in _TITLE_LEVEL_WORDS:
        words.pop(0)
    while words and (words[-1].lower() in _TITLE_LEVEL_SUFFIXES or words[-1].lower() in _TITLE_LEVEL_WORDS):
        words.pop()
    return words


def _split(title):
    # split "Data Scientist (Remote) - NLP Team" into its parts, dropping brackets
    if not title:
        return []
    title = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(title))
    return re.split(r"\s[-–—]\s|\||,|/|@|:|;", title)


def base_title(title):
    # the first part of a title that has any words left, e.g. "Sr. Data Eng. (Remote)" -> "Data Eng"
    for part in _split(title):
        words = _clean_words(part)
        if words:
            return " ".join(words)
    return ""

# job descriptions are long, so raise the csv field limit
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


# separators inside a title: " - ", "|", "/", "@", ":", ";", ","
SEPARATORS = r"\s[-–—]\s|[|/@:;,]"


def title_key(raw):
    # whole cleaned title: "Sr. Accountant, Tax" -> "accountant tax"
    return base_title(re.sub(SEPARATORS, " ", str(raw or ""))).lower()


def title_candidates(raw):
    # the whole title first, then each part: "Therapist / Counselor" -> [therapist counselor, therapist, counselor]
    candidates = [title_key(raw)]
    for part in re.split(SEPARATORS, str(raw or "")):
        key = base_title(part).lower()
        if key and key not in candidates:
            candidates.append(key)
    return [c for c in candidates if c]


def load_onet(alt_path, occ_path):
    # returns {cleaned title: O*NET occupation title}
    occupations = {}
    with open(occ_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            occupations[row["O*NET-SOC Code"]] = row["Title"].strip()

    lookup = {}
    for title in occupations.values():  # an occupation's own title comes first
        lookup.setdefault(title_key(title), title)

    clashes = 0
    with open(alt_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            role = occupations.get(row["O*NET-SOC Code"])
            if not role:
                continue
            for name in (row["Job Title"], row.get("Short Title")):
                key = title_key(name)
                if not key:
                    continue
                if lookup.setdefault(key, role) != role:
                    clashes += 1  # two occupations share this title, the first one kept it
    return lookup, clashes


def load_overrides(path):
    # returns {cleaned title: role}, empty if the file does not exist yet
    path = Path(path)
    if not path.exists():
        return {}
    overrides = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = {"title", "role"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path.name} is missing column(s): {', '.join(sorted(missing))}")
        for row in reader:
            key, role = title_key(row["title"]), (row["role"] or "").strip()
            if key and role:
                overrides[key] = role
    return overrides


def count_titles(postings_path, title_column):
    # returns ({cleaned title: number of postings}, {cleaned title: keys to try}), reading one row at a time
    with open(postings_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if title_column not in (reader.fieldnames or []):
            raise SystemExit(f"column '{title_column}' not found. Columns are: {', '.join(reader.fieldnames or [])}")
        raw_counts = Counter(row[title_column] for row in reader)
    counts, candidates = Counter(), {}
    for raw, n in raw_counts.items():  # clean each distinct title once, not every row
        key = title_key(raw)
        counts[key] += n
        candidates.setdefault(key, title_candidates(raw))
    return counts, candidates


def build_mapping(counts, candidates, onet, overrides):
    # returns rows of (title, role, source, postings), biggest first
    rows = []
    for key, n in counts.items():
        if not key:
            continue
        keys = candidates.get(key, [key])
        role, source = "", ""
        for k in keys:  # the team's overrides win over O*NET, for every candidate
            if k in overrides:
                role, source = overrides[k], "override"
                break
        if not role:
            for k in keys:
                if k in onet:
                    role, source = onet[k], "onet"
                    break
        rows.append((key, role, source, n))
    rows.sort(key=lambda r: (-r[3], r[0]))
    return rows


def write_mapping(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "role", "source", "postings"])
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS)
    parser.add_argument("--alt-titles", type=Path, default=DEFAULT_ALT_TITLES)
    parser.add_argument("--occupations", type=Path, default=DEFAULT_OCCUPATIONS)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING, help="where the mapping CSV is written")
    parser.add_argument("--title-column", default="title")
    args = parser.parse_args(argv)

    onet, clashes = load_onet(args.alt_titles, args.occupations)
    overrides = load_overrides(args.overrides)
    counts, candidates = count_titles(args.postings, args.title_column)
    rows = build_mapping(counts, candidates, onet, overrides)
    write_mapping(rows, args.mapping)

    total = sum(r[3] for r in rows)
    by_source = Counter()
    for _, _, source, n in rows:
        by_source[source or "unmapped"] += n
    print(f"{len(rows)} distinct cleaned titles, {len(set(r[1] for r in rows if r[1]))} roles, {len(overrides)} overrides")
    print(f"O*NET titles claimed by two occupations (first one kept): {clashes}")
    for source in ("override", "onet", "unmapped"):
        print(f"{source}: {by_source[source]} of {total} postings ({100 * by_source[source] / total:.1f}%)")
    print(f"wrote {args.mapping}")


if __name__ == "__main__":
    main()

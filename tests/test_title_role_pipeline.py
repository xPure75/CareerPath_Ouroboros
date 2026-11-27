import csv

import pytest

from build_title_roles import build_mapping, load_onet, load_overrides, title_candidates, title_key
from title_role_counts import level_from_title, level_from_years, load_mapping, process, resolve_level, ExperienceLevels


@pytest.fixture(scope="module")
def lv():
    return ExperienceLevels()


@pytest.fixture
def onet_files(tmp_path):
    occ = tmp_path / "occ.csv"
    occ.write_text("O*NET-SOC Code,Title,Description\n13-2011.00,Accountants and Auditors,x\n15-2051.00,Data Scientists,x\n", encoding="utf-8")
    alt = tmp_path / "alt.csv"
    alt.write_text(
        "O*NET-SOC Code,Title,Job Title,Short Title,Source(s)\n"
        "13-2011.00,Accountants and Auditors,Staff Accountant,,08\n"
        "13-2011.00,Accountants and Auditors,Tax Accountant,,08\n"
        "15-2051.00,Data Scientists,Data Analyst,,08\n",
        encoding="utf-8",
    )
    return alt, occ


def test_title_key_drops_level_words_and_numerals():
    assert title_key("Sr. Accountant") == title_key("Staff Accountant II") == "accountant"


def test_onet_alternate_titles_map_to_the_occupation(onet_files):
    onet, clashes = load_onet(*onet_files)
    assert onet[title_key("Tax Accountant")] == "Accountants and Auditors"
    assert onet[title_key("Data Analyst")] == "Data Scientists"
    assert clashes == 0


def test_overrides_win_over_onet(tmp_path, onet_files):
    onet, _ = load_onet(*onet_files)
    p = tmp_path / "o.csv"
    p.write_text("title,role\nData Analyst,Data Analyst\n", encoding="utf-8")
    rows = build_mapping({"data analyst": 5, "tax accountant": 3, "chef": 2}, {}, onet, load_overrides(p))
    assert rows == [("data analyst", "Data Analyst", "override", 5), ("tax accountant", "Accountants and Auditors", "onet", 3), ("chef", "", "", 2)]


def test_missing_overrides_file_is_fine(tmp_path):
    assert load_overrides(tmp_path / "nope.csv") == {}


@pytest.mark.parametrize(
    "label, title, desc, level, source",
    [
        ("Entry level", "Sr. Accountant", "", "Entry", "column"),
        ("Internship", "Accountant", "", "Entry", "column"),
        ("Associate", "Accountant", "10 years", "Mid", "column"),
        ("Director", "Accountant", "", "Senior", "column"),
        ("Executive", "Accountant", "", "Senior", "column"),
        ("Mid-Senior level", "Accountant", "Need 2 years", "Mid", "column"),
        ("Mid-Senior level", "Senior Accountant", "", "Senior", "column"),
        ("Mid-Senior level", "Accountant", "Need 7+ years", "Senior", "column"),
        ("", "Junior Accountant", "7 years", "Entry", "title"),
        ("", "Accountant", "3-5 years", "Mid", "description"),
        ("", "Accountant", "", None, "unknown"),
        (None, "Accountant", None, None, "unknown"),
    ],
)
def test_levels(lv, label, title, desc, level, source):
    assert resolve_level(label, title, desc, lv) == (level, source)


def test_staff_is_not_a_level_word():
    assert level_from_title("Staff Accountant") is None


def test_full_pass_gives_every_posting_a_row_and_counts_coverage(tmp_path, lv, onet_files):
    onet, _ = load_onet(*onet_files)
    mp = tmp_path / "m.csv"
    from build_title_roles import write_mapping
    write_mapping(build_mapping({"accountant": 2, "chef": 1}, {}, {**onet, "accountant": "Accountants and Auditors"}, {}), mp)
    postings = tmp_path / "p.csv"
    postings.write_text(
        'title,formatted_experience_level,description\nSr. Accountant,Entry level,x\n"Accountant, Tax",,3-5 years\nChef,,\n',
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    stats = process(postings, load_mapping(mp), lv, out, "title", "formatted_experience_level", "description")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == stats["total"] == 3
    assert [r["standard_title"] for r in rows] == ["Jr. Accountants and Auditors", "", ""]
    assert stats["mapped"] == 1 and stats["unmapped"]["accountant tax"] == 1


def test_title_candidates_try_the_whole_title_then_each_part():
    assert title_candidates("Therapist / Counselor") == ["therapist counselor", "therapist", "counselor"]
    assert title_candidates("Sr. Accountant") == ["accountant"]
    assert title_candidates("Retail Merchandiser - Electronics - Part Time") == [
        "retail merchandiser electronics part time", "retail merchandiser", "electronics", "part time"]


def test_a_later_part_of_the_title_can_match(onet_files):
    onet, _ = load_onet(*onet_files)
    key = title_key("Evening Shift / Tax Accountant")
    cands = {key: title_candidates("Evening Shift / Tax Accountant")}
    assert build_mapping({key: 1}, cands, onet, {})[0][1] == "Accountants and Auditors"


@pytest.mark.parametrize(
    "text, level",
    [
        ("With over 95 years of experience, we are great. Need 3+ years.", "Mid"),
        ("Founded 41 years of service ago", None),
        ("Must be 18 years of age or older", None),
        ("Must be 18 years old", None),
        ("High School Diploma required, 1 - 3 years of retail sales experience", "Entry"),
        ("10-15 years of experience", "Senior"),
        ("Minimum 5 yrs. on the job", "Mid"),
    ],
)
def test_level_from_years_skips_numbers_that_are_not_requirements(lv, text, level):
    assert level_from_years(text, lv) == level

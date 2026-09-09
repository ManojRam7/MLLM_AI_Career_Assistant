"""Deterministic apply-form autofill tests (no network/keys/LLM). Run with: pytest -q

These prove the profile-driven filler works for ANY employer's ATS — the matcher keys off field
LABELS and OPTIONS (identical across companies), never off a company name. Fixtures below imitate
Greenhouse, Workday, Ashby, SmartRecruiters, Lever and generic forms, plus the two real forms we
verified live (Ekimetrics, Octopus). If a common field regresses, one of these will fail."""
import json
import pathlib

from uk_jobops import applicant
from uk_jobops.applicant import _age_from_dob, _pick_option, _next_month_name, pick_radio_option
from uk_jobops.cv_match import match_cv_key, CV_LABEL

_ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Cfg:
    """Loads the REAL base_cv.json + data/applicant_profile.yaml so tests use the shipped profile."""
    base_cv = json.load(open(_ROOT / "src/uk_jobops/cv/base_cv.json"))
    profile = {}
    settings = {"apply": {"answers": {}}}

    def path(self, rel):
        return str(_ROOT / rel)


def fill(fields):
    """Return {apply_id(str): value} from the deterministic matcher."""
    return applicant.deterministic_fill(_Cfg(), fields)


def _t(i, label):
    return {"apply_id": i, "kind": "text", "label": label}


def _s(i, label, options):
    return {"apply_id": i, "kind": "select", "label": label, "options": options}


def _r(i, label, options):
    return {"apply_id": i, "kind": "radio", "label": label,
            "options": [{"apply_id": i * 100 + j, "text": o} for j, o in enumerate(options)]}


# --------------------------------------------------------------------------- helpers
def test_age_from_dob():
    # 7 Feb 1999 → 27 in Sep 2026 (birthday already passed this year at test authoring)
    assert _age_from_dob("07/02/1999") in {"27", "28"}   # tolerant of test date
    assert _age_from_dob("nonsense") == ""


def test_pick_option_prefers_exact():
    assert _pick_option("Man", ["Woman", "Man", "Non-binary"]) == "Man"
    assert _pick_option("Male", ["Man", "Woman"]) is None       # no fuzzy male→man; caller lists both


def test_next_month_is_a_month():
    assert _next_month_name() in applicant._MONTHS


def test_radio_exact_match_beats_substring():
    # THE Octopus bug: 'Man' must NOT tick 'Woman' (which contains 'man')
    opts = [{"apply_id": 1, "text": "Woman"}, {"apply_id": 2, "text": "Man"},
            {"apply_id": 3, "text": "Non-binary"}]
    assert pick_radio_option(opts, "Man") == 2
    assert pick_radio_option(opts, "Woman") == 1
    # long RTW option matched exactly, never the shorter 'permanent'/'national' ones
    rtw = [{"apply_id": 1, "text": "I'm a UK or Irish National"},
           {"apply_id": 2, "text": "I have a visa that gives me permanent right to work in the UK"},
           {"apply_id": 3, "text": "I have a visa that gives me temporary right to work in the UK, "
                                   "and might need sponsorship in the future"}]
    want = ("I have a visa that gives me temporary right to work in the UK, "
            "and might need sponsorship in the future")
    assert pick_radio_option(rtw, want) == 3


# --------------------------------------------------------------------------- CV routing (any role)
def test_cv_match_routes_by_role():
    cases = {
        "Data Analyst (Credit)": "Data Analyst",
        "Senior Data Analyst": "Senior Data Analyst",
        "Analytics Engineer": "Analytics Engineer",
        "AI Engineer": "AI Engineer",
        "Machine Learning Engineer": "AI Engineer",
        "Data Scientist & Marketing Effectiveness Consultant (MMM)": "Data Scientist (Azure)",
        "Lead Data Scientist": "Lead Data Scientist",
        "Senior Data Scientist": "Senior Data Scientist",
    }
    for title, want in cases.items():
        assert CV_LABEL[match_cv_key(title, "", "")] == want, title


# --------------------------------------------------------------------------- identity/contact (any ATS)
def test_identity_and_contact():
    a = fill([
        _t(0, "Full name *"), _t(1, "First name"), _t(2, "Last name"), _t(3, "Email *"),
        _t(4, "Phone *"), _t(5, "LinkedIn Profile"), _t(6, "GitHub"), _t(7, "Website / Portfolio"),
        _t(8, "Current location *"), _t(9, "Current company"), _t(10, "Preferred name"),
        _s(11, "Pronouns", ["Select...", "He/Him", "She/Her", "They/Them"]),
    ])
    assert a["0"] == "Manoj Ram Mopati"
    assert a["1"] == "Manoj" and a["2"] == "Mopati"
    assert a["3"] == "manojrammopati111@gmail.com"
    assert a["4"] == "07810294727"
    assert "linkedin" in a["5"].lower()
    assert "github" in a["6"].lower()
    assert a["8"] == "London, United Kingdom"      # location autocomplete → geocodable value
    assert a["9"] == "Tahir Group"
    assert a["10"] == "Manoj"
    assert a["11"] == "He/Him"


# --------------------------------------------------------------------------- work authorisation variants
def test_work_auth_yes_no_and_sponsorship():
    a = fill([
        _s(0, "Are you legally authorized to work in the United Kingdom?", ["Yes", "No"]),
        _s(1, "Do you currently require visa sponsorship?", ["Yes", "No"]),
        _s(2, "Will you now or in the future require sponsorship for employment?", ["Yes", "No"]),
    ])
    assert a["0"] == "Yes"      # eligible now (Graduate visa)
    assert a["1"] == "No"       # no sponsorship needed now
    assert a["2"] == "Yes"      # honest: will need it in the future (visa expires 2028)


def test_work_auth_status_picker_graduate():
    # Octopus-style radio: must pick the TEMPORARY / graduate option, never permanent/national
    a = fill([_r(0, "What's your current right to work status?", [
        "I'm a UK or Irish National",
        "I have indefinite leave to remain or settled status",
        "I have a visa that gives me permanent right to work in the UK",
        "I have a visa that gives me temporary right to work in the UK, and might need sponsorship in the future",
        "I will need visa sponsorship to start this role",
    ])])
    assert a["0"] == ("I have a visa that gives me temporary right to work in the UK, "
                      "and might need sponsorship in the future")


def test_work_auth_graduate_select():
    a = fill([_s(0, "What is your Right to Work status in the UK?", [
        "Please select…", "I have the full unrestricted right to work in the UK (e.g. British Citizen)",
        "I am on a Student Visa and will need a graduate Visa", "I have a Graduate Visa",
        "I do not currently have the right to work in the UK"])])
    assert a["0"] == "I have a Graduate Visa"          # not the 'Student…will need a graduate' option


# --------------------------------------------------------------------------- EEO / diversity (any ATS)
def test_diversity_man_woman_and_male_female():
    a = fill([
        _s(0, "Gender", ["Prefer not to say", "Man", "Woman", "Non-binary"]),
        _s(1, "Please select your gender identity", ["Select...", "Male", "Female", "Non Binary"]),
    ])
    assert a["0"] == "Man"
    assert a["1"] == "Male"


def test_diversity_ethnicity_specific_then_generic():
    a = fill([
        _s(0, "Ethnicity", ["Prefer not to say", "Asian or Asian British - Indian",
                            "Asian or Asian British - Other", "White", "Black", "Mixed"]),
        _s(1, "Ethnic origin", ["Select...", "White", "Asian", "Black", "Mixed", "Other"]),
    ])
    assert a["0"] == "Asian or Asian British - Indian"   # specific Indian option preferred
    assert a["1"] == "Asian"                              # generic fallback when no Indian option


def test_octopus_diversity_radios():
    # the three that were wrong on Octopus: gender→Man, neurodiverse→No, and RTW→temporary
    a = fill([
        _r(0, "What gender do you identify as?",
           ["Woman", "Man", "Non-binary", "I identify in another way", "I'd prefer not to say"]),
        _r(1, "Do you identify as neurodiverse?", ["Yes", "No", "I'd prefer not to say"]),
        _r(2, "What's your current right to work status?", [
            "I'm a UK or Irish National", "I have indefinite leave to remain or settled status",
            "I have a visa that gives me permanent right to work in the UK",
            "I have a visa that gives me temporary right to work in the UK, and might need sponsorship in the future",
            "I will need visa sponsorship to start this role"]),
        _r(3, "Which race or ethnicity best describes you?",
           ["White", "Asian", "Black or African", "Hispanic, Latino, or Spanish origin",
            "Native Hawaiian or other Pacific Islander", "Other race, ethnicity, or origin", "I'd prefer not to say"]),
    ])
    assert a["0"] == "Man"                               # not 'Woman'
    assert a["1"] == "No"                                # not 'Yes'
    assert a["2"].startswith("I have a visa that gives me temporary")
    assert a["3"] == "Asian"


def test_background_and_health_all_clean():
    a = fill([
        _r(0, "Do you have any unspent criminal convictions?", ["Yes", "No"]),
        _r(1, "Are you willing to undergo a DBS / background check?", ["Yes", "No"]),
        _r(2, "Do you have any long-term health conditions?", ["Yes", "No", "Prefer not to say"]),
        _r(3, "Do you identify as neurodiverse?", ["Yes", "No", "Prefer not to say"]),
        _r(4, "Do you consider yourself to have a disability?", ["Yes", "No", "Prefer not to say"]),
    ])
    assert a["0"] == "No"      # clean record
    assert a["1"] == "Yes"     # happy to be checked
    assert a["2"] == "No"      # good health
    assert a["3"] == "No"      # not neurodiverse
    assert a["4"] == "No"      # no disability


def test_unknown_radio_left_to_llm():
    # an arbitrary Yes/No radio must NOT be blind-guessed 'Yes' — left blank for the LLM
    a = fill([_r(0, "Are you happy to complete a short assessment?", ["Yes", "No"])])
    assert "0" not in a


def test_first_gen_and_gender_identity():
    a = fill([
        _r(0, "Are you the first in your family to attend university?", ["Yes", "No", "Prefer not to say"]),
        _r(1, "Is your gender identity the same as the sex you were assigned at birth?",
           ["Yes", "No", "Prefer not to say"]),
        _r(2, "Do you identify as transgender?", ["Yes", "No", "Prefer not to say"]),
    ])
    assert a["0"] == "No"        # parents are graduates → not first-generation
    assert a["1"] == "Yes"       # cisgender
    assert a["2"] == "No"


def test_diversity_orientation_age_disability_veteran():
    a = fill([
        _s(0, "Sexual orientation", ["Prefer not to say", "Heterosexual/Straight", "Gay or Lesbian", "Bisexual"]),
        _s(1, "Age", ["Select...", "16-24", "25-29", "30-34", "35-44"]),
        _s(2, "Do you have a disability?", ["Yes", "No", "Prefer not to say"]),
        _s(3, "Veteran status", ["I am not a veteran", "I am a veteran", "Prefer not to say"]),
    ])
    assert a["0"] == "Heterosexual/Straight"
    assert a["1"] == "25-29"
    assert a["2"] == "No"
    assert a["3"] == "I am not a veteran"


# --------------------------------------------------------------------------- background / personal
def test_personal_dob_nationality_country_parents():
    a = fill([
        _t(0, "Date of birth (DD/MM/YYYY)"),
        _t(1, "What is your nationality?"),
        _t(2, "Country of birth"),
        _s(3, "Country", ["Select...", "United Kingdom", "United States", "India", "France"]),  # address block
        _s(4, "Did either of your parents attend university?", ["Select...", "Yes", "No", "Prefer not to say"]),
        _t(5, "Occupation of your main household earner when you were 14"),
    ])
    assert a["0"] == "07/02/1999"
    assert a["1"] == "Indian"
    assert a["2"] == "India"
    assert a["3"] == "United Kingdom"      # a bare address 'Country' = where he lives now
    assert a["4"] == "Yes"
    assert a["5"] == "Teacher"


# --------------------------------------------------------------------------- address & location
def test_address_and_location():
    a = fill([
        _t(0, "Current location *"),          # Lever autocomplete → geocodable "London, United Kingdom"
        _t(1, "Address line 1"),
        _t(2, "Address line 2"),
        _t(3, "Town / City"),
        _t(4, "Postcode"),
        _t(5, "County"),
        _s(6, "Country", ["Select...", "United Kingdom", "India", "United States"]),
    ])
    assert a["0"] == "London, United Kingdom"
    assert a["1"] == "Flat 14, Johnson House Apartments"
    assert a["2"] == "Florida Street"
    assert a["3"] == "London"
    assert a["4"] == "E2 6AN"
    assert a["5"] == "Greater London"
    assert a["6"] == "United Kingdom"


# --------------------------------------------------------------------------- role logistics
def test_notice_start_salary_relocate_heard():
    a = fill([
        _s(0, "Notice period", ["Select...", "1 month", "2 months", "3 months", "Available now"]),
        _s(1, "Ideal start date", ["Select..."] + applicant._MONTHS),
        _t(2, "Expected salary"),
        _t(3, "Minimum salary expectation"),
        _s(4, "Are you willing to relocate?", ["Yes", "No"]),
        _s(5, "How did you hear about us?", ["Select...", "LinkedIn", "Indeed", "Company website", "Referral"]),
        _r(6, "Preferred coding language (Python or R)?", ["Python", "R", "Other"]),
    ])
    assert a["0"] == "1 month"
    assert a["1"] in applicant._MONTHS               # picks the next calendar month
    assert a["2"].startswith("£")
    assert a["3"].startswith("£")
    assert a["4"] == "Yes"
    assert a["5"] == "LinkedIn"
    assert a["6"] == "Python"


# --------------------------------------------------------------------------- consent & skips
def test_consent_checkbox_and_skip_if_other():
    a = fill([
        {"apply_id": 0, "kind": "checkbox", "label": "I consent to my data being retained (GDPR)"},
        _t(1, "If other, please specify"),
    ])
    assert a["0"] == "yes"
    assert "1" not in a                              # 'if other' left blank


# --------------------------------------------------------------------------- real forms (regression)
def test_ekimetrics_regression():
    fields = [
        _t(0, "Full name ✱"), _t(1, "Email ✱"), _t(2, "Phone ✱"), _t(3, "Current location ✱"),
        _t(4, "Current company"),
        _s(5, "What is your Right to Work status in the UK?", [
            "Please select the option that applies to you.",
            "I have the full unrestricted right to work in the UK (e.g. British Citizen)",
            "I am on a Student Visa and will need a graduate Visa", "I have a Graduate Visa",
            "I do not currently have the right to work in the UK"]),
        _s(10, "What is your notice period? ✱", ["Select...", "1 month", "2 months", "3 months"]),
        _s(11, "What is your ideal start date ? ✱", ["Select..."] + applicant._MONTHS),
        _t(12, "What is your expected salary range ? ✱"),
        _s(13, "How did you hear about Ekimetrics ? ✱", ["Select...", "Website", "Job board", "Social media"]),
        _s(14, "Ekimetrics is committed to being a diverse and inclusive organisation...",
           ["Gender", "Male", "Female", "Non Binary", "Prefer not to say"]),
        _s(15, "Ethnicity", ["Select...", "White", "Asian", "Mixed", "Black", "Other"]),
        _s(16, "Age Bracket", ["Select...", "16-24", "25-29", "30-34"]),
        {"apply_id": 18, "kind": "checkbox", "label": "Yes, I consent to Ekimetrics retaining my data"},
        _r(7, "Preferred coding language", ["Python", "R", "Other"]),
    ]
    a = fill(fields)
    assert a["0"] == "Manoj Ram Mopati"
    assert a["5"] == "I have a Graduate Visa"
    assert a["12"].startswith("£")
    assert a["14"] == "Male"
    assert a["15"] == "Asian"
    assert a["16"] == "25-29"
    assert a["18"] == "yes"
    assert a["7"] == "Python"
    # every field except the (absent) 'if other' should be answered
    assert len(a) >= 13

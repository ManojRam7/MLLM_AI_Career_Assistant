"""Applicant brain — the single "answer like me" knowledge source for the AI apply agent.

It fuses three things into one plain-text brief the LLM reasons from when filling ANY form:
  1. data/applicant_profile.yaml  — the hand-written master profile (identity, work auth, prefs,
     experience, question bank in the user's own voice)  → the primary source of truth.
  2. the exact CV .docx being attached for THIS job                → so answers match the CV on file.
  3. settings apply.answers + base_cv/profile.json                 → back-compat / extra facts.

No network, no DB. Pure file reads + docx text extraction, so it is trivially testable and runs
identically on the Mac and in CI. If the YAML is missing it degrades gracefully to settings/base_cv.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import zipfile
import re

try:
    import yaml  # PyYAML ships with the project (config.py uses it)
except Exception:  # pragma: no cover
    yaml = None

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


# --------------------------------------------------------------------------- profile file
def load_profile(cfg) -> dict:
    """Load data/applicant_profile.yaml. Returns {} if absent/unparseable (never raises)."""
    if yaml is None:
        return {}
    p = pathlib.Path(cfg.path("data/applicant_profile.yaml"))
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- .docx -> plain text
def cv_text(cv_path: str, max_chars: int = 6000) -> str:
    """Extract readable text from a .docx CV (no external deps: read word/document.xml directly).

    Falls back to '' on any error so a missing/locked CV never breaks the apply run."""
    if not cv_path:
        return ""
    p = pathlib.Path(cv_path)
    if not p.exists() or p.suffix.lower() != ".docx":
        return ""
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    # drop drawing/picture blocks first (they hold EMU coordinate numbers that glue onto real text)
    xml = re.sub(r"<w:drawing\b.*?</w:drawing>", " ", xml, flags=re.S)
    xml = re.sub(r"<w:pict\b.*?</w:pict>", " ", xml, flags=re.S)
    # split paragraphs on </w:p> and tabs on </w:tab>, strip tags, decode the few entities we care about
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", " ")
    text = re.sub(r"<[^>]+>", "", xml)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'"))
    lines = [ln.strip() for ln in text.splitlines()]
    out = "\n".join(ln for ln in lines if ln)
    return out[:max_chars].strip()


# --------------------------------------------------------------------------- build the brain
def _yaml_block(profile: dict) -> str:
    """Render the master profile back to compact YAML the LLM can read (or JSON if PyYAML absent)."""
    if not profile:
        return ""
    if yaml is not None:
        try:
            return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=100).strip()
        except Exception:
            pass
    return json.dumps(profile, ensure_ascii=False, indent=1)


def build_brain(cfg, cv_path: str = "", cv_label: str = "", job: dict | None = None) -> str:
    """Return the full candidate brief the apply agent reasons from.

    Layers, in priority order the agent is told to respect:
      MASTER PROFILE (applicant_profile.yaml) > THIS CV's text > extra facts (base_cv/settings)."""
    profile = load_profile(cfg)
    parts: list[str] = []

    if profile:
        parts.append("=== MASTER PROFILE (primary source of truth — answer as this person) ===")
        parts.append(_yaml_block(profile))

    # exact CV being attached for this job
    txt = cv_text(cv_path)
    if txt:
        parts.append(f"\n=== CV ATTACHED FOR THIS APPLICATION ({cv_label or 'role CV'}) ===")
        parts.append("Answers about experience/skills must be consistent with this CV:\n" + txt)

    # extra structured facts from base_cv + profile.json (back-compat / anything not in the YAML)
    ct = cfg.base_cv.get("contact", {})
    extra = {
        "name": cfg.base_cv.get("name", ""), "email": ct.get("email", ""),
        "phone": ct.get("phone", ""), "location": ct.get("location", "London, UK"),
        "linkedin": ct.get("linkedin", ""), "github": ct.get("github", ""),
        "portfolio": ct.get("portfolio", ""), "headline": cfg.profile.get("headline", ""),
        "skills": cfg.base_cv.get("skills", {}), "profile_facts": cfg.base_cv.get("profile_facts", []),
    }
    parts.append("\n=== EXTRA STRUCTURED FACTS (use only if not covered above) ===")
    parts.append(json.dumps({k: v for k, v in extra.items() if v}, ensure_ascii=False))

    # settings apply.answers — legacy standard answers (lowest priority; master profile wins on conflict)
    ans = cfg.settings.get("apply", {}).get("answers", {})
    if ans:
        parts.append("\n=== STANDARD ANSWERS (fallback for exact-match questions) ===")
        parts.append(json.dumps(ans, ensure_ascii=False))

    # this specific job (so 'why this company/role' AND tech/business answers are tailored to the JD)
    if job:
        jd = {"role": job.get("title", ""), "company": job.get("company", ""),
              "location": job.get("locations") or job.get("location") or "",
              "country": job.get("country", "")}
        parts.append("\n=== THIS ROLE (tailor EVERY open answer to this — mirror its wording/stack) ===")
        parts.append(json.dumps({k: v for k, v in jd.items() if v}, ensure_ascii=False))
        desc = (job.get("description") or "").strip()
        if desc:
            parts.append("\n--- JOB DESCRIPTION (use its exact keywords/tech where I truly have them) ---")
            parts.append(desc[:1800])

    return "\n".join(parts)


# ============================================================================
# DETERMINISTIC FORM-FILL — map common application fields straight from the profile,
# WITHOUT any LLM call, so the form fills even if the model errors. The LLM is left to
# handle only genuinely open questions (cover letter / "why this role") the matcher skips.
# ============================================================================
def _opt_texts(field: dict) -> list[str]:
    """Options as plain strings (select = str list; radio = list of {text})."""
    out = []
    for o in field.get("options") or []:
        out.append(o if isinstance(o, str) else str(o.get("text", "")))
    return [o for o in out if o is not None]


_PLACEHOLDER = ("select", "please select", "gender", "ethnicity", "age bracket",
                "--", "choose", "select...", "select an option")


def _pick_option(desired: str, options: list[str]) -> str | None:
    """Return the option whose text best matches `desired` (exact > substring), or None."""
    if not desired or not options:
        return None
    dl = desired.strip().lower()
    for o in options:                                   # exact
        if o.strip().lower() == dl:
            return o
    for o in options:                                   # substring either way
        ol = o.strip().lower()
        if not ol or ol in _PLACEHOLDER:
            continue
        if dl in ol or ol in dl:
            return o
    return None


def _first_present(prefs: list[str], options: list[str]) -> str | None:
    for p in prefs:
        o = _pick_option(p, options)
        if o:
            return o
    return None


def _opt_has(opts_l: list[str], *subs: str) -> bool:
    """True if any option text contains any of the substrings (case-insensitive)."""
    return any(any(s in o for o in opts_l) for s in subs)


def pick_radio_option(options: list[dict], value: str):
    """Given radio options [{apply_id, text}, ...] pick the apply_id whose text matches `value`.
    EXACT (case-insensitive) match FIRST, then substring — so 'Man' never ticks 'Woman'
    (which contains 'man'). Returns the apply_id, or None if nothing matches."""
    vl = str(value).strip().lower()
    if not vl:
        return None
    for o in options:
        if (o.get("text", "") or "").strip().lower() == vl:
            return o.get("apply_id")
    for o in options:
        t = (o.get("text", "") or "").strip().lower()
        if t and (vl in t or t in vl):
            return o.get("apply_id")
    return None


def _next_month_name() -> str:
    m = _dt.date.today().month  # 1-12; next month, wrapping
    return _MONTHS[m % 12]


def _age_from_dob(dob: str) -> str:
    """dob 'DD/MM/YYYY' -> current age as a string; '' if unparseable."""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            d = _dt.datetime.strptime(dob.strip(), fmt).date()
            t = _dt.date.today()
            return str(t.year - d.year - ((t.month, t.day) < (d.month, d.day)))
        except Exception:
            continue
    return ""


def answer_key(cfg) -> dict:
    """Canonical facts used by the deterministic matcher (profile > base_cv > settings)."""
    prof = load_profile(cfg)
    ident = prof.get("identity", {})
    wauth = prof.get("work_authorization", {})
    pref = prof.get("preferences", {})
    div = prof.get("diversity", {})
    sa = prof.get("standard_answers", {})
    pers = prof.get("personal", {})
    bg = prof.get("background", {})
    addr = prof.get("address", {})
    ct = cfg.base_cv.get("contact", {})
    ans = cfg.settings.get("apply", {}).get("answers", {})

    def salary_text() -> str:
        v = pref.get("salary_target_gbp") or ans.get("salary_expectation_gbp") or 45000
        try:
            v = int(str(v).replace(",", "").replace("£", "").strip())
            return f"£{v-5000:,} - £{v+5000:,}"
        except Exception:
            return "£40,000 - £50,000"

    return {
        "name": ident.get("full_name") or cfg.base_cv.get("name", ""),
        "email": ident.get("email") or ct.get("email", ""),
        "phone": ident.get("phone") or ct.get("phone", ""),
        "location": ident.get("location") or ct.get("location", "London, UK"),
        "city_location": pers.get("city_location") or "London, United Kingdom",
        "addr_line1": addr.get("line1") or "",
        "addr_line2": addr.get("line2") or "",
        "city": addr.get("city") or "London",
        "county": addr.get("county") or "Greater London",
        "postcode": addr.get("postcode") or "",
        "addr_full": addr.get("full") or "",
        "linkedin": ident.get("linkedin") or ct.get("linkedin", ""),
        "github": ident.get("github") or ct.get("github", ""),
        "portfolio": ident.get("portfolio") or ct.get("portfolio", ""),
        "current_company": sa.get("current_company") or ans.get("current_company", "Tahir Group"),
        "notice": sa.get("notice_period") or ans.get("notice_period") or pref.get("notice_period") or "1 month",
        "salary": salary_text(),
        "gender": div.get("gender") or ans.get("gender") or "Male",
        "ethnicity": div.get("ethnicity") or ans.get("ethnicity") or "Indian",
        "age": div.get("age_bracket") or ans.get("age_bracket") or "25-29",
        "disability": div.get("disability") or ans.get("disability") or "No",
        "orientation": div.get("sexual_orientation") or "Heterosexual (straight)",
        "coding": sa.get("coding_language") or ans.get("coding_language") or "Python",
        "heard": sa.get("heard_about_role") or ans.get("heard_about_role") or "LinkedIn",
        "rtw_uk": wauth.get("right_to_work_uk") or ans.get("right_to_work") or "Yes",
        "years": sa.get("years_experience") or ans.get("years_experience") or "4+",
        "dob": pers.get("date_of_birth") or "",
        "nationality": pers.get("nationality") or "Indian",
        "home_country": pers.get("home_country") or pers.get("country_of_birth") or "India",
        "current_country": pers.get("current_country") or "United Kingdom",
        "parents_he": bg.get("parents_higher_education") or "Yes",
        "parent_occupation": bg.get("father_occupation") or bg.get("main_earner_occupation") or "",
        "pronouns": ident.get("pronouns") or "He/Him",
        "title": ident.get("title") or "Mr",
        "relocate": pref.get("willing_to_relocate") or ans.get("willing_to_relocate") or "Yes",
        "salary_min": pref.get("salary_min_gbp") or 40000,
        "salary_max": pref.get("salary_max_gbp") or 55000,
    }


def deterministic_fill(cfg, fields: list[dict]) -> dict:
    """Return {apply_id: value} for every field we can answer WITHOUT the LLM.

    value = exact option text for select/radio, 'yes' for consent checkboxes, text otherwise."""
    k = answer_key(cfg)
    out: dict[str, str] = {}
    for f in fields:
        lab = (f.get("label") or "").lower()
        kind = f.get("kind")
        aid = str(f.get("apply_id"))
        opts = _opt_texts(f)
        opts_l = [o.lower() for o in opts]
        val = None

        # diversity selects often have an unhelpful label — detect them from their OPTIONS too
        # (note: 'man' is a substring of 'woman', 'male' of 'female' — so require BOTH sides present)
        # exclude yes/no 'is your gender the same as birth' / 'transgender' — those are handled separately
        _gender_excl = any(x in lab for x in ("same as", "assigned at birth", "transgender",
                                              "identity the same", "same as your sex", "same as the sex"))
        is_gender = (("gender" in lab and not _gender_excl)
                     or (_opt_has(opts_l, "woman", "female") and _opt_has(opts_l, "man", "male")))
        is_ethnic = ("ethnic" in lab or "race" in lab
                     or (_opt_has(opts_l, "asian") and _opt_has(opts_l, "white", "black", "mixed")))
        is_age = (bool(re.search(r"\bage\b", lab))
                  or any(re.search(r"\d\d\s*[-–]\s*\d\d", o) for o in opts)
                  or _opt_has(opts_l, "16-24", "18-24"))
        is_orient = ("orientation" in lab
                     or _opt_has(opts_l, "heterosexual", "bisexual", "lesbian", "gay"))

        if "if other" in lab or "please specify" in lab or "if yes" in lab:
            continue  # only relevant if a linked select was 'Other' — leave blank
        elif "full name" in lab or (re.search(r"\bname\b", lab) and not any(
                x in lab for x in ("first", "last", "sur", "given", "middle", "preferred", "user",
                                   "file", "referr", "company", "nick", "known as", "maiden"))):
            val = k["name"]
        elif "first name" in lab or "given name" in lab:
            val = k["name"].split()[0]
        elif "last name" in lab or "surname" in lab or "family name" in lab:
            val = k["name"].split()[-1]
        elif "email" in lab:
            val = k["email"]
        elif "phone" in lab or "mobile" in lab or "telephone" in lab or "contact number" in lab:
            val = k["phone"]
        elif "preferred name" in lab or "preferred first name" in lab or "known as" in lab or "nickname" in lab:
            val = k["name"].split()[0]
        elif "pronoun" in lab:
            val = _first_present([k["pronouns"], "He/Him", "He / Him", "He/him"], opts) or k["pronouns"]
        elif "salutation" in lab or "prefix" in lab or (re.search(r"\btitle\b", lab)
              and _opt_has(opts_l, "mr", "mrs", "ms", "dr", "mx")):
            val = _first_present([k["title"], "Mr"], opts) or k["title"]
        elif "current company" in lab or "employer" in lab or ("company" in lab and "why" not in lab and "cover" not in lab):
            val = k["current_company"]
        elif "linkedin" in lab:
            val = k["linkedin"]
        elif "github" in lab:
            val = k["github"]
        elif "portfolio" in lab or "website" in lab or "personal site" in lab:
            val = k["portfolio"]
        elif "postcode" in lab or "postal code" in lab or "post code" in lab or re.search(r"\bzip\b", lab):
            val = k["postcode"]
        elif ("address line 2" in lab or "address line2" in lab or "address 2" in lab
              or "apartment" in lab or "suite" in lab or re.search(r"\bunit\b", lab)):
            val = k["addr_line2"]
        elif ("address line 1" in lab or "address line1" in lab or "street address" in lab
              or "address 1" in lab or "street name" in lab):
            val = k["addr_line1"]
        elif re.search(r"\baddress\b", lab) and "email" not in lab and "ip " not in lab and "url" not in lab:
            val = k["addr_full"] or k["addr_line1"]
        elif re.search(r"\b(county|region|province)\b", lab):
            val = k["county"]
        elif ("current location" in lab or "location" in lab or "based" in lab
              or "where do you live" in lab or "where are you based" in lab):
            val = k["city_location"]                    # autocomplete location field → geocodable value
        elif re.search(r"\b(city|town)\b", lab) and "capacity" not in lab:
            val = k["city"]
        elif "nationality" in lab or "citizenship" in lab or "what is your citizen" in lab:
            val = _first_present([k["nationality"], "Indian", "India"], opts) if opts else k["nationality"]
        elif ("country of birth" in lab or "country of origin" in lab or "home country" in lab
              or "where are you from" in lab
              or ("country" in lab and ("origin" in lab or "born" in lab) and "work" not in lab and "sponsor" not in lab)):
            val = _first_present([k["home_country"], "India"], opts) if opts else k["home_country"]
        elif ("country of residence" in lab or "current country" in lab
              or ("country" in lab and ("live" in lab or "reside" in lab or "based" in lab or "current" in lab))
              or (re.fullmatch(r"country\s*[*✱]?", lab.strip()) is not None)
              or ("country" in lab and _opt_has(opts_l, "united kingdom", "united states"))):
            val = _first_present([k["current_country"], "United Kingdom", "UK"], opts) if opts else k["current_country"]
        elif (("date of birth" in lab or "birthday" in lab or re.search(r"\bdob\b", lab)
               or ("birth" in lab and ("date" in lab or "born" in lab)))
              and "assigned" not in lab and "gender" not in lab and "place" not in lab and "country" not in lab):
            val = k["dob"]
        elif is_orient:
            val = _first_present([k["orientation"], "Heterosexual", "Straight",
                                  "Heterosexual/Straight"], opts) or k["orientation"]
        elif ("parent" in lab or "guardian" in lab) and ("graduat" in lab or "degree" in lab
              or "university" in lab or "higher education" in lab or "qualification" in lab):
            val = _first_present([k["parents_he"], "Yes"], opts) or k["parents_he"]
        elif "occupation" in lab and ("parent" in lab or "guardian" in lab or "household" in lab
              or "earner" in lab or "sole" in lab or "main" in lab or "family" in lab):
            val = k["parent_occupation"] or None
        elif ("right to work" in lab or ("authori" in lab and "work" in lab) or "eligible to work" in lab
              or "entitled to work" in lab or "permission to work" in lab or "legally able to work" in lab
              or "visa" in lab or "sponsor" in lab or "work permit" in lab):
            # is this a STATUS-CHOICE picker (visa/citizen options) or a yes/no eligibility question?
            is_status = (_opt_has(opts_l, "visa", "citizen", "settled", "graduate", "indefinite",
                                  "national", "right to work") or any(len(o) > 40 for o in opts))
            if is_status:
                # Graduate visa = TEMPORARY right to work + may need sponsorship later.
                # Prefer the graduate/temporary option; NEVER 'permanent'/'indefinite'/'national'.
                val = _first_present(["I have a Graduate Visa", "have a Graduate Visa", "Graduate Visa",
                                      "temporary right to work in the UK, and might need sponsorship",
                                      "temporary right to work", "might need sponsorship in the future",
                                      "full unrestricted right to work", "unrestricted right to work",
                                      "right to work", "Yes"], opts) or k["rtw_uk"]
            elif "sponsor" in lab and ("require" in lab or "need" in lab):
                # "do you require sponsorship" → No now; but "...now OR in the future" → honest Yes
                base = "Yes" if "future" in lab else "No"
                val = _first_present([base], opts) or base
            else:                                       # plain "are you authorised/eligible to work?" → Yes
                val = _first_present(["Yes", "I have the right to work"], opts) or "Yes"
        elif "notice period" in lab or "notice" in lab:
            val = _pick_option(k["notice"], opts) or k["notice"]
        elif "start date" in lab or "ideal start" in lab or "availab" in lab or "when can you start" in lab:
            if any(m.lower() in [o.lower() for o in opts] for m in _MONTHS):
                val = _pick_option(_next_month_name(), opts)
            else:
                val = _first_present([k["notice"], "1 month", "Available now", "Immediately",
                                      "Within 1 month"], opts) or ("Within 1 month of an offer" if kind == "text" else None)
        elif ("salary" in lab or "compensation" in lab or "remuneration" in lab) and ("minimum" in lab or "lowest" in lab or re.search(r"\bmin\b", lab) or "from" in lab):
            val = f"£{int(k['salary_min']):,}"
        elif ("salary" in lab or "compensation" in lab or "remuneration" in lab) and ("maximum" in lab or "highest" in lab or re.search(r"\bmax\b", lab) or "up to" in lab or "upto" in lab):
            val = f"£{int(k['salary_max']):,}"
        elif "salary" in lab or "compensation" in lab or "remuneration" in lab or "expected pay" in lab:
            val = k["salary"]
        elif ("hear about" in lab or "did you hear" in lab or "how did you find" in lab
              or "source" in lab or ("referr" in lab and kind == "select")):
            val = _first_present([k["heard"], "LinkedIn", "Job board", "Website",
                                  "Social media", "Social Media or Jobs site", "Referral", "Other"], opts) or k["heard"]
        elif is_gender:
            # forms may say Male OR Man — treat as the same; never fall to Woman/Female
            val = _first_present([k["gender"], "Male", "Man"], opts) or k["gender"]
        elif is_ethnic:
            # prefer the specific 'Indian' option, else fall back to a generic Asian option
            val = _first_present([k["ethnicity"], "Indian", "Asian or Asian British - Indian",
                                  "Asian Indian", "South Asian", "Asian"], opts) or k["ethnicity"]
        elif is_age:
            if kind == "text" and k["dob"]:            # a numeric age box → compute from DOB
                val = _age_from_dob(k["dob"]) or k["age"]
            else:                                      # an age-bracket select
                val = _pick_option(k["age"], opts) or k["age"]
        elif "disab" in lab:
            val = _first_present([k["disability"], "No", "Prefer not to say"], opts) or k["disability"]
        elif "neurodiver" in lab or "neurodivergent" in lab:
            val = _first_present(["No", "I'd prefer not to say", "Prefer not to say"], opts) or "No"
        elif ("gender identity the same" in lab or "same as the sex" in lab
              or "same as your sex assigned" in lab or "gender the same as" in lab
              or "gender you were assigned" in lab):
            val = _first_present(["Yes"], opts) or "Yes"        # cisgender → Yes
        elif "transgender" in lab or ("trans" in lab and "identify" in lab):
            val = _first_present(["No", "I'd prefer not to say", "Prefer not to say"], opts) or "No"
        elif ("first generation" in lab or ("first in" in lab and ("universit" in lab or "family" in lab))
              or ("first" in lab and "family" in lab and ("universit" in lab or "degree" in lab))):
            val = _first_present(["No"], opts) or "No"          # parents are graduates → not first-gen
        elif ("long-term health" in lab or "long term health" in lab or "health condition" in lab
              or "long-term illness" in lab or "long term illness" in lab):
            val = _first_present(["No", "I'd prefer not to say", "Prefer not to say"], opts) or "No"
        elif ("caring responsibilit" in lab or re.search(r"\bcarer\b", lab)) and kind in ("select", "radio"):
            val = _first_present(["No", "I'd prefer not to say", "Prefer not to say"], opts) or "No"
        elif ("religion" in lab or "faith" in lab or "religious belief" in lab) and kind in ("select", "radio"):
            val = _first_present(["Prefer not to say", "I'd prefer not to say", "None", "No religion"], opts)
        elif "relocat" in lab or "willing to move" in lab or "open to relocation" in lab:
            val = _first_present([k["relocate"], "Yes"], opts) or k["relocate"]
        elif "veteran" in lab or "armed forces" in lab or "military service" in lab:
            val = _first_present(["No", "I am not a veteran", "Not a veteran", "N/A", "Prefer not to say"], opts) or "No"
        elif ("background check" in lab or re.search(r"\bdbs\b", lab) or "reference check" in lab
              or "vetting" in lab or "pre-employment screen" in lab or "pre employment screen" in lab):
            val = _first_present(["Yes", "I consent", "I agree", "Happy to"], opts) or "Yes"
        elif ("criminal" in lab or "convict" in lab or "unspent" in lab or "offence" in lab
              or "offense" in lab or "caution" in lab or ("police" in lab and "record" in lab)):
            val = _first_present(["No", "None"], opts) or "No"
        elif "years of experience" in lab or "years experience" in lab or "how many years" in lab:
            val = k["years"]
        elif kind == "radio" and (_opt_has(opts_l, "python", "sql", "scala", "java")
                                  or "coding" in lab or "programming" in lab or "language" in lab):
            # a coding-language radio → Python. (Unknown radios are left to the LLM — never blind-guess 'Yes'.)
            val = _first_present([k["coding"], "Python"], opts)
        elif kind == "checkbox" and any(x in lab for x in
                                        ("consent", "agree", "gdpr", "privacy", "retain", "terms",
                                         "permission", "declare", "confirm")):
            val = "yes"

        if not val:
            continue
        if kind == "select":                       # only fill a select if an option actually matches
            m = _pick_option(str(val), opts)
            if not m:
                continue
            val = m
        out[aid] = val
    return out

"""Geography + visa-sponsorship intelligence for the GLOBAL search (v2).

Two jobs, both deterministic (no LLM, no network) so they run on every job at zero cost:
  1. country + priority tier   — UK (tier 1) > EU/EEA/CH (tier 2) > rest of world (tier 3)
  2. visa-sponsorship signal    — sponsors / likely / refused / unknown

Manoj is on a UK Graduate visa (works in the UK with NO sponsorship). Any non-UK role needs
sponsorship, so for non-UK we PREFER employers that explicitly sponsor or are likely sponsors
(big multinationals / global banks / Big Tech / large consultancies). We never hard-drop a non-UK
role for being silent on sponsorship — we just rank it below UK and below explicit/likely sponsors."""
from __future__ import annotations

import re

# --- country -> canonical name, via country names AND signature cities (order: specific first) ---
# tier 1 = UK, tier 2 = EU/EEA + Switzerland, tier 3 = rest of world.
_UK = (r"united kingdom|\bu\.?k\.?\b|england|scotland|wales|northern ireland|london|manchester|"
       r"birmingham|leeds|glasgow|edinburgh|bristol|cardiff|liverpool|sheffield|newcastle|"
       r"nottingham|southampton|brighton|coventry|reading|oxford|cambridge|belfast|leicester|"
       r"aberdeen|dundee|swansea|milton keynes")

# canonical -> (tier, regex). Checked in order; UK first so 'London, UK' never mis-tags.
_COUNTRIES: list[tuple[str, int, str]] = [
    ("United Kingdom", 1, _UK),
    ("Ireland", 2, r"\bireland\b|dublin|cork|galway"),
    ("Germany", 2, r"germany|deutschland|berlin|munich|münchen|frankfurt|hamburg|cologne|stuttgart|düsseldorf"),
    ("Netherlands", 2, r"netherlands|holland|amsterdam|rotterdam|the hague|eindhoven|utrecht"),
    ("France", 2, r"\bfrance\b|paris|lyon|toulouse|montrouge|lille|marseille"),
    ("Spain", 2, r"\bspain\b|españa|madrid|barcelona|valencia|malaga|seville"),
    ("Portugal", 2, r"portugal|lisbon|lisboa|porto"),
    ("Italy", 2, r"\bitaly\b|milan|rome|roma|turin|bologna"),
    ("Belgium", 2, r"belgium|brussels|antwerp|machelen|ghent"),
    ("Luxembourg", 2, r"luxembourg"),
    ("Switzerland", 2, r"switzerland|zurich|zürich|geneva|basel|lausanne|zug"),
    ("Sweden", 2, r"sweden|stockholm|gothenburg|malmö"),
    ("Denmark", 2, r"denmark|copenhagen|aarhus"),
    ("Norway", 2, r"norway|oslo|bergen"),
    ("Finland", 2, r"finland|helsinki|espoo"),
    ("Poland", 2, r"poland|warsaw|warszawa|krak[oó]w|krakow|wroc[lł]aw|gda[nń]sk|pozna[nń]"),
    ("Austria", 2, r"austria|vienna|wien|graz"),
    ("Czech Republic", 2, r"czech|prague|praha|brno"),
    ("Romania", 2, r"romania|bucharest|cluj"),
    ("Greece", 2, r"greece|athens|thessaloniki"),
    ("Estonia", 2, r"estonia|tallinn"),
    ("Lithuania", 2, r"lithuania|vilnius"),
    ("Latvia", 2, r"latvia|riga"),
    ("United States", 3, r"united states|\bu\.?s\.?a\.?\b|\bu\.?s\.?\b|new york|san francisco|"
                         r"seattle|austin|boston|chicago|dallas|atlanta|silicon valley|california|texas"),
    ("Canada", 3, r"canada|toronto|vancouver|montreal|ottawa|calgary|waterloo"),
    ("Australia", 3, r"australia|sydney|melbourne|brisbane|perth|canberra"),
    ("New Zealand", 3, r"new zealand|auckland|wellington"),
    ("UAE", 3, r"\buae\b|united arab emirates|dubai|abu dhabi"),
    ("Singapore", 3, r"singapore"),
    ("Malaysia", 3, r"malaysia|kuala lumpur"),
    ("Russia", 3, r"russia|moscow|saint petersburg|st petersburg"),
    ("India", 3, r"\bindia\b|bengaluru|bangalore|hyderabad|pune|chennai|gurgaon|gurugram|noida|mumbai|delhi|kolkata"),
    ("Hong Kong", 3, r"hong kong"),
    ("Japan", 3, r"\bjapan\b|tokyo|osaka"),
    ("Qatar", 3, r"qatar|doha"),
    ("Saudi Arabia", 3, r"saudi|riyadh|jeddah"),
    ("Brazil", 3, r"brazil|são paulo|sao paulo|rio de janeiro"),
    ("Mexico", 3, r"mexico|méxico|guadalajara"),
    ("South Africa", 3, r"south africa|johannesburg|cape town"),
]
_COMPILED = [(name, tier, re.compile(pat, re.I)) for (name, tier, pat) in _COUNTRIES]

# Adzuna country-code endpoints (subset it supports), tiered for the priority sweep.
ADZUNA_CODES = {
    "United Kingdom": "gb", "Ireland": "ie", "Germany": "de", "Netherlands": "nl", "France": "fr",
    "Spain": "es", "Italy": "it", "Poland": "pl", "Austria": "at", "Switzerland": "ch",
    "United States": "us", "Canada": "ca", "Australia": "au", "New Zealand": "nz", "Singapore": "sg",
    "India": "in", "Brazil": "br", "Mexico": "mx", "South Africa": "za",
}


def detect_country(text: str, default: str = "") -> str:
    """Best-guess country name from a location/title/description blob (UK wins ties)."""
    t = text or ""
    for name, _tier, rx in _COMPILED:
        if rx.search(t):
            return name
    return default


def country_tier(country: str) -> int:
    """1 = UK, 2 = EU/EEA/CH, 3 = rest of world, 0 = unknown."""
    if not country:
        return 0
    for name, tier, _rx in _COMPILED:
        if name.lower() == country.lower():
            return tier
    # fall back to matching the string against the patterns (handles ISO/loose names)
    for name, tier, rx in _COMPILED:
        if rx.search(country):
            return tier
    return 0


# --- visa sponsorship ---
_SPONSOR = re.compile(
    r"(visa sponsorship|will sponsor|can sponsor|sponsorship (is )?(available|offered|provided)|"
    r"we sponsor|offer sponsorship|skilled worker visa|tier 2 (visa|sponsor)|certificate of sponsorship|"
    r"\bblue card\b|work permit (provided|support|assistance)|relocation (package|support|assistance|"
    r"provided|offered)|willing to sponsor|sponsor(ship)? for the right candidate|global mobility|"
    # widened: the schemes employers actually name in EU/global postings
    r"highly skilled migrant|kennismigrant|critical skills( employment)? permit|employment pass|"
    r"work(ing)? (visa|permit) (sponsor|support|provided)|visa support|we support relocation|"
    r"relocation assistance|international candidates (are )?welcome|open to international|"
    r"express entry|lmia|subclass 482|sponsor(ed|ship)? work permit|immigration support)", re.I)

# Countries whose tech/data market routinely hires internationally via a well-known skilled-migration
# route (NL highly-skilled migrant, DE/EU Blue Card, IE critical skills, CA express entry, AU/NZ skilled,
# SG employment pass, UAE work permit). A role in one of these is a REALISTIC sponsorship target even
# when the (usually truncated) advert text never mentions a visa — so treat it as 'likely', not 'unknown'.
_SPONSOR_FRIENDLY_COUNTRIES = {
    "Netherlands", "Germany", "Ireland", "Austria", "Belgium", "Luxembourg", "Denmark", "Sweden",
    "Finland", "Norway", "Switzerland", "France", "Poland", "Spain", "Portugal", "Czechia",
    "Canada", "Australia", "New Zealand", "Singapore", "UAE", "United Arab Emirates", "Japan",
}
_REFUSE = re.compile(
    r"(no (visa )?sponsorship|not able to sponsor|unable to sponsor|cannot sponsor|do(es)? not (offer|"
    r"provide) sponsorship|without sponsorship|must (already )?(have|hold) (the )?(right to work|work "
    r"authori[sz]ation|valid work)|must be (legally )?authori[sz]ed to work|no relocation)", re.I)

# Employers that reliably sponsor / run global-mobility programmes (Big Tech, global banks, top
# consultancies, large multinationals). Substring match on the normalised company name.
_LIKELY_SPONSORS = {
    "google", "microsoft", "amazon", "meta", "apple", "netflix", "nvidia", "openai", "anthropic",
    "ibm", "oracle", "sap", "salesforce", "adobe", "intel", "qualcomm", "uber", "spotify", "stripe",
    "booking", "adyen", "palantir", "databricks", "snowflake", "servicenow", "atlassian", "datadog",
    "jpmorgan", "j.p. morgan", "goldman sachs", "morgan stanley", "citi", "citigroup", "barclays",
    "hsbc", "deutsche bank", "ubs", "credit suisse", "bnp paribas", "bank of america", "wells fargo",
    "blackrock", "bloomberg", "capital one", "american express", "mastercard", "visa inc", "paypal",
    "revolut", "wise", "n26", "klarna", "checkout.com",
    "accenture", "deloitte", "pwc", "kpmg", "ey", "ernst", "mckinsey", "bcg", "bain", "capgemini",
    "tcs", "tata consultancy", "infosys", "wipro", "cognizant", "ntt data", "thoughtworks",
    "astrazeneca", "gsk", "glaxosmithkline", "novartis", "roche", "pfizer", "unilever", "nestlé",
    "nestle", "procter", "p&g", "reckitt", "diageo", "shell", "bp ", "siemens", "bosch", "philips",
    "vodafone", "ericsson", "nokia", "samsung", "sony", "huawei", "bytedance", "tiktok", "grab",
    "sea limited", "shopee", "delivery hero", "zalando",
}


def visa_signal(text: str, company: str = "", country: str = "") -> str:
    """'sponsors' (explicit) > 'refused' (explicit no) > 'likely' (known sponsor employer, OR a country
    with a standard skilled-migration route) > 'unknown'.

    `country` matters because most adverts we ingest are SNIPPETS (SERP/Adzuna), so the visa wording is
    usually absent — judging on text alone marked almost everything 'unknown' and the strict ingest gate
    then deleted every international job. Country context makes the signal realistic."""
    t = text or ""
    if _SPONSOR.search(t):
        return "sponsors"
    if _REFUSE.search(t):
        return "refused"
    c = re.sub(r"[^a-z0-9 &.]", "", (company or "").lower())
    if any(s in c for s in _LIKELY_SPONSORS):
        return "likely"
    if (country or "").strip() in _SPONSOR_FRIENDLY_COUNTRIES:
        return "likely"
    return "unknown"


# ranking helpers -----------------------------------------------------------
_VISA_RANK = {"sponsors": 3, "likely": 2, "unknown": 1, "refused": 0}


def visa_rank(signal: str) -> int:
    return _VISA_RANK.get(signal or "unknown", 1)


def geo_score(country: str, visa: str) -> int:
    """A small additive priority score used to rank the global feed:
    UK dominates; within non-UK, EU > world and explicit/likely sponsors rise. UK roles need no
    sponsorship so their visa signal is ignored. Range roughly 0..120."""
    tier = country_tier(country)
    if tier == 1:                       # UK — top, visa irrelevant
        return 100
    if tier == 0:                       # unknown location — treat as neutral-low
        return 40 + visa_rank(visa) * 3
    base = 60 if tier == 2 else 40      # EU above rest-of-world
    if visa == "refused":               # explicit 'no sponsorship' abroad — strong penalty
        return base - 25
    return base + visa_rank(visa) * 6   # sponsors/likely lift the score

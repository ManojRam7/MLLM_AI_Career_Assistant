"""Deterministic (token-free) CV-keyword extraction — the 2.0 recommendation core.

For EVERY job we pull the ATS keywords that matter for THAT posting and split them into:
  - include  : keywords the job asks for that Manoj genuinely evidences  -> put these in the CV
  - familiar : keywords he has secondary/working exposure to (Azure-first, GCP/AWS familiar)
  - missing  : keywords the job asks for that he does NOT evidence       -> gaps to be aware of

No LLM call — a curated skills vocabulary is regex-matched against the job description, ranked by
importance x frequency, and filtered against the candidate's real skill set (from profile.json +
base CV). This runs for all jobs at zero token cost, so the dashboard/Telegram can show, per job,
exactly which words to weave into the CV."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# (canonical label, weight, regex). Higher weight = more important to surface first.
# Weight bands: 5 = Manoj's differentiators / core stack, 4 = key tools, 3 = common tools,
# 2 = techniques, 1 = generic/soft. Patterns are matched case-insensitively with word gates.
_VOCAB: list[tuple[str, int, str]] = [
    # --- domain differentiators (highest signal for his profile) ---
    ("Marketing Mix Modelling", 5, r"marketing mix model|\bmmm\b|media mix model"),
    ("Marketing Science", 5, r"marketing science|marketing analytics"),
    ("Price Elasticity", 5, r"price elasticit|elasticit(y|ies)"),
    ("Pricing Analytics", 5, r"pricing (analyt|strateg|model|optimis)|revenue management|revenue growth management|\brgm\b"),
    ("Trade Promotion Optimisation", 5, r"trade[- ]promotion|promotion optimis|trade spend"),
    ("Forecasting", 4, r"forecast|demand planning|demand model"),
    ("Time Series", 4, r"time[- ]series"),
    ("Segmentation", 4, r"segmentation|customer segment|\brfm\b"),
    ("Churn Modelling", 4, r"churn|reten(tion|ded) model|attrition"),
    ("Risk & Fraud Analytics", 4, r"fraud|credit risk|risk analyt|\baml\b|financial crime|underwriting"),
    ("Claims Analytics", 3, r"claims analyt|claims model|insurance analyt"),
    ("Customer Analytics", 3, r"customer analyt|customer insight|\bclv\b|lifetime value|propensity"),
    ("Experimentation / A/B Testing", 3, r"a/?b test|experimentation|hypothesis test|randomis(ed|ation)|causal inference"),
    ("Recommendation Systems", 3, r"recommend(er|ation) system|personalis(ation|ed)"),
    # --- languages ---
    ("Python", 5, r"\bpython\b"),
    ("SQL", 5, r"\bsql\b|t-sql|pl/?sql|ansi sql"),
    ("R", 3, r"\br programming\b|\br language\b|python[ ,/]+r\b|\br[ ,/]+python|\br shiny\b|rstudio"),
    ("Scala", 2, r"\bscala\b"),
    ("SAS", 2, r"\bsas\b"),
    # --- python / ml libraries ---
    ("scikit-learn", 4, r"scikit[- ]?learn|sklearn"),
    ("pandas / NumPy", 3, r"\bpandas\b|\bnumpy\b"),
    ("TensorFlow / PyTorch", 3, r"tensorflow|pytorch|\bkeras\b"),
    ("XGBoost / LightGBM", 3, r"xgboost|lightgbm|gradient boost|\bcatboost\b"),
    ("spaCy / Transformers", 3, r"\bspacy\b|hugging ?face|\btransformers\b"),
    # --- big data ---
    ("PySpark / Spark", 5, r"pyspark|apache spark|\bspark\b"),
    ("Databricks", 4, r"databricks"),
    ("Kafka", 2, r"\bkafka\b"),
    ("Hadoop / Hive", 2, r"\bhadoop\b|\bhive\b"),
    # --- cloud: Azure (primary) ---
    ("Azure", 4, r"\bazure\b|microsoft azure"),
    ("Azure Data Factory", 3, r"data factory|\badf\b"),
    ("Azure ML", 3, r"azure ml|azure machine learning|\baml studio\b"),
    ("Azure Synapse", 2, r"synapse"),
    ("Data Lake", 2, r"data lake|delta lake"),
    ("Microsoft Fabric", 3, r"microsoft fabric|\bfabric\b"),
    # --- cloud: GCP / AWS (familiar) ---
    ("GCP", 3, r"\bgcp\b|google cloud"),
    ("BigQuery", 4, r"bigquery|big query"),
    ("Vertex AI", 2, r"vertex ai"),
    ("Looker", 3, r"\blooker\b(?! studio)"),
    ("AWS", 3, r"\baws\b|amazon web services"),
    ("SageMaker", 2, r"sagemaker"),
    ("Redshift", 2, r"redshift"),
    ("Snowflake", 3, r"snowflake"),
    # --- data engineering ---
    ("ETL / ELT", 3, r"\betl\b|\belt\b|data pipeline|ingestion pipeline"),
    ("dbt", 3, r"\bdbt\b"),
    ("Airflow", 2, r"airflow"),
    ("Data Warehousing", 3, r"data warehous|data warehouse|dimensional model|star schema"),
    ("Data Modelling", 3, r"data model(l)?ing|data model\b"),
    ("Data Quality", 2, r"data quality|data governance|data validation"),
    # --- BI / viz ---
    ("Power BI", 5, r"power ?bi"),
    ("Tableau", 4, r"tableau"),
    ("Looker Studio", 2, r"looker studio|data studio"),
    ("Qlik", 2, r"\bqlik"),
    ("DAX / Power Query", 2, r"\bdax\b|power query|\bm query\b"),
    ("Excel", 1, r"\bexcel\b|advanced excel|\bvba\b"),
    ("Dashboards / Data Viz", 2, r"dashboard|data visualis|reporting suite|self[- ]serve analytics"),
    # --- ML / AI techniques ---
    ("Machine Learning", 4, r"machine learning|\bml\b(?!ops)|predictive model|statistical learning"),
    ("Deep Learning", 2, r"deep learning|neural network"),
    ("NLP", 3, r"\bnlp\b|natural language|text mining|text analytics"),
    ("Computer Vision", 2, r"computer vision|image (recognition|classification)"),
    ("GenAI / LLM", 4, r"generative ai|gen ?ai|\bllm(s)?\b|large language model|prompt engineering"),
    ("RAG / LangChain", 4, r"\brag\b|retrieval[- ]augmented|langchain|vector (db|database|store)|\bfaiss\b|embeddings?"),
    ("MLOps", 3, r"mlops|model deployment|model monitoring|feature store|model serving"),
    ("Feature Engineering", 3, r"feature engineering|feature selection"),
    ("Statistical Modelling", 3, r"statistical model|regression analysis|bayesian|\bglm\b|generalis(ed) linear"),
    ("Optimisation / OR", 3, r"optimis(ation|er)|operations research|linear programming|or-tools|constraint solv"),
    # --- eng / ops ---
    ("Docker", 2, r"docker|container(is|iz)ed"),
    ("Kubernetes", 2, r"kubernetes|\bk8s\b"),
    ("Git / CI-CD", 2, r"\bgit\b|ci/?cd|github actions|version control|devops"),
    ("FastAPI / REST", 2, r"fastapi|rest api|restful|flask"),
    ("Streamlit", 1, r"streamlit"),
    ("Agile / Jira", 1, r"\bagile\b|scrum|\bjira\b|kanban"),
    # --- soft ---
    ("Stakeholder Management", 2, r"stakeholder|business partner|cross[- ]functional"),
    ("Data Storytelling", 2, r"storytelling|data[- ]driven decision|insight[- ]to[- ]action|present(ing|ation) (to|of)"),
    ("Requirements Gathering", 1, r"requirements gathering|business requirements|elicit"),
]

_COMPILED = [(label, w, re.compile(pat, re.I)) for (label, w, pat) in _VOCAB]

# Labels the honest_gaps note says NOT to claim without a concrete example (forced into 'missing'
# even if a fuzzy profile match occurs). Keeps recommendations truthful.
_HARD_GAP = {"BigQuery", "Looker", "dbt", "Snowflake", "Redshift", "SageMaker", "Vertex AI"}


@dataclass
class Keywords:
    include: list[str] = field(default_factory=list)    # JD wants + he evidences -> put in CV
    familiar: list[str] = field(default_factory=list)   # JD wants + working/secondary familiarity
    missing: list[str] = field(default_factory=list)    # JD wants + genuine gap (awareness only)

    def to_line(self) -> str:
        """Compact single-line summary stored in the DB / shown in Telegram."""
        parts = []
        if self.include:
            parts.append("CV keywords: " + ", ".join(self.include))
        if self.familiar:
            parts.append("(familiar: " + ", ".join(self.familiar) + ")")
        if self.missing:
            parts.append("JD also wants (gaps): " + ", ".join(self.missing))
        return "  ·  ".join(parts)

    def to_dict(self) -> dict:
        return {"include": self.include, "familiar": self.familiar, "missing": self.missing}


def candidate_skills(profile: dict | None, base_cv: dict | None = None) -> dict[str, str]:
    """Map each vocabulary label the candidate genuinely has to 'have' or 'familiar', by matching
    the vocab against the profile's evidenced/strong-signal skills (have) and the secondary/familiar
    list (familiar). Everything else is treated as a gap when a JD asks for it."""
    profile = profile or {}
    have_blob = " ".join(str(x) for x in (
        (profile.get("skills_primary_evidenced") or [])
        + (profile.get("strong_positive_signals") or [])
        + (profile.get("certifications") or [])
        + [profile.get("headline", "")]
        + [json.dumps(base_cv) if base_cv else ""]))
    fam_blob = " ".join(str(x) for x in (profile.get("skills_secondary_familiar") or []))
    out: dict[str, str] = {}
    for label, _w, rx in _COMPILED:
        if rx.search(have_blob):
            out[label] = "have"
        elif rx.search(fam_blob):
            out[label] = "familiar"
    return out


def extract_keywords(job_text: str, cand: dict[str, str], *, max_include: int = 12,
                     max_missing: int = 6) -> Keywords:
    """Rank the vocabulary keywords present in this job description by importance x frequency, then
    split into include / familiar / missing using the candidate's real skill map."""
    text = job_text or ""
    scored: list[tuple[float, str]] = []
    for label, w, rx in _COMPILED:
        hits = len(rx.findall(text))
        if hits:
            scored.append((w * (1 + min(hits, 4) * 0.25), label))
    scored.sort(key=lambda t: (-t[0], t[1]))

    include, familiar, missing = [], [], []
    for _s, label in scored:
        status = cand.get(label, "")
        if label in _HARD_GAP and status != "have":       # never over-claim hard gaps
            missing.append(label)
        elif status == "have":
            include.append(label)
        elif status == "familiar":
            familiar.append(label)
        else:
            missing.append(label)
    return Keywords(include=include[:max_include], familiar=familiar[:4], missing=missing[:max_missing])

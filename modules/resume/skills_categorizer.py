# modules/resume/skills_categorizer.py

"""
Central skill categorization logic for CareerAI.

Takes a flat list of skills (dicts or strings) and returns a dict:

{
  "Programming": [...],
  "Data Libraries": [...],
  "Visualization": [...],
  "Data Engineering": [...],
  "Databases": [...],
  "Tools": [...],
  "Cloud": [...],
  "Soft Skills": [...],
  "Other": [...]
}

This is used by:
- Profile Portal (skills container)
- Resume Skill Extractor 2.0
- Skill Mapper v2
- Dream Planner
"""

from typing import Any, Dict, List

# NOTE:
# We keep the SAME 9 buckets (Programming, Data Libraries, Visualization,
# Data Engineering, Databases, Tools, Cloud, Soft Skills, Other) to stay
# aligned with the CareerAI blueprint. We only expand keyword coverage.


CATEGORY_KEYWORDS = {
    "Programming": [
        # Core languages
        "python",
        "java ",
        " java",
        "javascript",
        "js ",
        " js",
        "typescript",
        "ts ",
        " ts",
        "c++",
        "c#",
        "golang",
        "go ",
        " go,",
        "ruby",
        "php",
        "scala",
        "kotlin",
        "rust",
        "swift",
        "matlab",
        "r ",
        " r,",
        "sas ",

        # Web / scripting
        "html",
        "css",
        "sass",
        "less",

        # Frameworks / runtimes (we still treat as programming skills)
        "react",
        "next.js",
        "nextjs",
        "angular",
        "vue",
        "vue.js",
        "nuxt",
        "svelte",
        "sveltekit",
        "jquery",
        "node.js",
        "nodejs",
        "express",
        "django",
        "flask",
        "fastapi",
        "spring",
        "spring boot",
        "laravel",
        "symfony",
        "rails",
        "ruby on rails",
        ".net",
        "dotnet",
    ],
    "Data Libraries": [
        "pandas",
        "numpy",
        "numPy",
        "scikit-learn",
        "sklearn",
        "tensorflow",
        "keras",
        "pytorch",
        "pyTorch",
        "xgboost",
        "lightgbm",
        "statsmodels",
        "spacy",
        "spaCy",
        "nltk",
        "transformers",
        "hugging face",
        "huggingface",
        "prophet",
    ],
    "Visualization": [
        "power bi",
        "powerbi",
        "tableau",
        "looker",
        "looker studio",
        "google data studio",
        "data studio",
        "superset",
        "metabase",
        "qlik",
        "qlikview",
        "qliksense",
        "matplotlib",
        "plotly",
        "seaborn",
        "ggplot",
        "ggplot2",
        "grafana",
        "redash",
    ],
    "Data Engineering": [
        "airflow",
        "apache airflow",
        "dbt",
        "data build tool",
        "azure data factory",
        "adf",
        "kafka",
        "apache kafka",
        "spark",
        "pyspark",
        "hadoop",
        "hive",
        "pig",
        "flink",
        "storm",
        "snowpipe",
        "fivetran",
        "informatica",
        "talend",
        "etl ",
        " etl",
        "elt ",
        " elt",
    ],
    "Databases": [
        "mysql",
        "mariadb",
        "postgres",
        "postgresql",
        "sql server",
        "mssql",
        "oracle",
        "db2",
        "sqlite",
        "mongodb",
        "mongo",
        "dynamodb",
        "cassandra",
        "redis",
        "couchdb",
        "elasticsearch",
        "snowflake",
        "redshift",
        "bigquery",
        "cosmos db",
        "cosmosdb",
        "neo4j",
        "graphdb",
        "timescaledb",
        "time series database",
    ],
    "Tools": [
        # Dev tools
        "git",
        "github",
        "gitlab",
        "bitbucket",
        "svn",
        "mercurial",

        # DevOps / CI tools (kept under Tools to preserve 9-category model)
        "docker",
        "kubernetes",
        "k8s",
        "helm",
        "terraform",
        "ansible",
        "chef",
        "puppet",
        "jenkins",
        "circleci",
        "teamcity",
        "travis",
        "bamboo",

        # Collaboration / productivity
        "jira",
        "confluence",
        "notion",
        "trello",
        "asana",
        "clickup",
        "slack",
        "microsoft teams",

        # APIs / testing
        "postman",
        "soapui",
        "newman",

        # Office / spreadsheets
        "excel",
        "ms excel",
        "microsoft excel",
        "sheets",
        "google sheets",
        "powerpoint",
        "word",

        # Design tools
        "figma",
        "adobe xd",
        "sketch",
        "canva",
    ],
    "Cloud": [
        # Platforms
        "aws",
        "amazon web services",
        "azure",
        "gcp",
        "google cloud",
        "google cloud platform",

        # AWS services
        "ec2",
        "s3",
        "lambda",
        "ecs",
        "eks",
        "rds",
        "cloudwatch",
        "api gateway",
        "dynamodb",

        # Azure services
        "azure functions",
        "app service",
        "cosmos db",
        "azure devops",
        "aks",

        # GCP services
        "app engine",
        "cloud run",
        "cloud functions",
        "bigquery",
        "cloud storage",
    ],
    "Soft Skills": [
        "communication",
        "presentation",
        "public speaking",
        "storytelling",
        "teamwork",
        "collaboration",
        "leadership",
        "mentoring",
        "coaching",
        "problem solving",
        "problem-solving",
        "analytical thinking",
        "critical thinking",
        "time management",
        "stakeholder management",
        "negotiation",
        "conflict resolution",
        "adaptability",
        "ownership",
        "attention to detail",
    ],
}


def _extract_name(item: Any) -> str:
    """
    Accept either a dict like {"name": "..."} or a plain string.
    Returns a clean skill name or "".
    """
    if isinstance(item, dict):
        name = (
            item.get("name")
            or item.get("skill")
            or item.get("title")
            or ""
        )
        return str(name).strip()
    return str(item).strip()


def categorize_skills(skills_list: List[Any]) -> Dict[str, list]:
    """
    Given a list of skills (dicts or strings), return a dict of categories.

    Any skill that doesn't match a category keyword goes under "Other".
    Category keys with no skills are omitted.

    IMPORTANT:
    - This NEVER drops a skill.
    - Unknown skills are still surfaced under "Other".
    """
    buckets = {cat: [] for cat in CATEGORY_KEYWORDS.keys()}
    other: list = []

    for item in skills_list or []:
        name = _extract_name(item)
        if not name:
            continue

        lower = name.lower()
        matched = False

        for category, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                # Simple substring match; can be refined later if needed.
                if kw in lower:
                    if name not in buckets[category]:
                        buckets[category].append(name)
                    matched = True
                    break
            if matched:
                break

        if not matched:
            if name not in other:
                other.append(name)

    result: Dict[str, list] = {}

    # Only include non-empty categories
    for cat, names in buckets.items():
        if names:
            result[cat] = names

    if other:
        result["Other"] = other

    return result

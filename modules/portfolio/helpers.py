import os

MOCK = os.getenv("MOCK", "1") == "1"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def build_deep_portfolio(title: str, role: str) -> str:
    """
    Returns rich HTML chunks for the portfolio body.
    """
    if MOCK or not os.getenv("OPENAI_API_KEY"):
        return f"""
        <section class="space-y-6">
          <header>
            <h1 class="text-4xl font-extrabold">{title}</h1>
            <p class="opacity-80">Target Role: {role} · Deep Briefs</p>
          </header>
          <article class="glass p-4 rounded-xl">
            <h3 class="font-bold">Case Study 1 — Metric‑driven feature</h3>
            <p>Problem → Approach → Results (add screenshots & metrics)</p>
          </article>
          <article class="glass p-4 rounded-xl">
            <h3 class="font-bold">Case Study 2 — Data pipeline & dashboard</h3>
            <p>Dataset → ETL → KPI visuals → Insights → Next steps</p>
          </article>
          <div class="text-sm opacity-75">Rubric: Clear problem statements, measurable outcomes, reproducibility links.</div>
        </section>
        """.strip()

    try:
        from openai import OpenAI
        client = OpenAI()
        prompt = f"""Create HTML (no <html> or <body>) for a premium portfolio section for the title "{title}" targeting "{role}".
Include: header, 2 case-study sections (problem→solution→metrics), and a rubric line."""
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role":"user","content":prompt}],
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "<p>Deep portfolio could not be generated. Try again later.</p>"

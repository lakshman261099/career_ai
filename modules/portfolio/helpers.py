import re, os, uuid, datetime as dt

TEMPLATES = {
"generic": """
<section class="py-8">
  <h1 class="text-4xl font-extrabold mb-2">{title}</h1>
  <p class="opacity-80 mb-6">Role: {role}</p>
  <div class="grid md:grid-cols-3 gap-4">
    <div class="glass p-4 card">
      <h3 class="font-bold">Mini‑Project 1</h3>
      <p>Define a problem, collect data, and ship a concise deliverable (notebook, mockups, or deck).</p>
    </div>
    <div class="glass p-4 card">
      <h3 class="font-bold">Mini‑Project 2</h3>
      <p>Explore a company product and propose an improvement with a simple metric.</p>
    </div>
    <div class="glass p-4 card">
      <h3 class="font-bold">Mini‑Project 3</h3>
      <p>Summarize impact in 3 STAR bullets with quantified outcomes.</p>
    </div>
  </div>
</section>
"""
}

def slugify(s:str)->str:
    s = re.sub(r'[^a-zA-Z0-9\- ]','',s).strip().lower().replace(" ","-")
    return s[:50] or str(uuid.uuid4())[:8]

def build_page_html(title, role):
    body = TEMPLATES["generic"].format(title=title, role=role)
    shell = f"""<!doctype html><html><head>
      <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
      <script src="https://cdn.tailwindcss.com"></script>
      <style>.gradient-bg{{background:linear-gradient(135deg,#6d28d9,#7c3aed,#a78bfa);}} .glass{{background:rgba(255,255,255,0.08);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.15);}} .card{{border-radius:16px;}}</style>
      <title>{title} — Portfolio</title></head>
      <body class="min-h-screen text-white gradient-bg">
      <main class="max-w-4xl mx-auto p-6">{body}</main>
      </body></html>"""
    return shell

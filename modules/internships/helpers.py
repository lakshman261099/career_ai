import os, json, random

USE_MOCK = os.getenv("MOCK", "1") == "1"

FREE_COURSES = {
    "SQL":"https://www.khanacademy.org/computing/computer-programming/sql",
    "Python":"https://www.freecodecamp.org/learn/scientific-computing-with-python/",
    "Statistics":"https://www.khanacademy.org/math/statistics-probability",
    "Communication":"https://www.coursera.org/learn/wharton-communication-skills",
}

def mock_fetch(role, location):
    base = [
        {"title":"Software Engineer Intern","company":"Acme","link":"https://example.com/acme-intern","source":"Mock"},
        {"title":"Data Analyst Intern","company":"Dataco","link":"https://example.com/dataco","source":"Mock"},
        {"title":"Product Intern","company":"BrightApps","link":"https://example.com/bright","source":"Mock"},
        {"title":"ML Intern","company":"Visionary","link":"https://example.com/visionary","source":"Mock"},
    ]
    res=[]
    for j in base:
        score = random.randint(55,95)
        gaps = random.sample(list(FREE_COURSES.keys()), k=random.randint(1,2))
        res.append({**j,"match_score":score,"missing_skills":gaps})
    return res

def compute_learning_links(skills):
    return [{"skill":s,"link":FREE_COURSES.get(s,"https://www.google.com/search?q="+s+"+course")} for s in skills]

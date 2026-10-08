from helpers import DEEP, THIN, score


def test_deep_agentic_outscores_thin_wrapper():
    deep, _ = score(DEEP, corpus=[DEEP, THIN])
    thin, _ = score(THIN, corpus=[DEEP, THIN])
    assert deep.breakdown.ai_project_depth > thin.breakdown.ai_project_depth + 15


def test_thin_wrapper_gets_penalty_between_5_and_15():
    thin, _ = score(THIN)
    pts = sum(p.points for p in thin.penalties)
    assert 5 <= pts <= 15 and any("Thin" in p.reason for p in thin.penalties)


def test_deep_project_has_no_wrapper_penalty():
    deep, _ = score(DEEP)
    assert not any("Thin" in p.reason for p in deep.penalties)


def test_breakdown_respects_category_caps():
    deep, _ = score(DEEP)
    b = deep.breakdown
    assert b.ai_project_depth <= 40 and b.python_backend <= 30 and b.cloud_fullstack <= 15
    assert b.engineering_depth <= 5 and b.github == 0   # github is added after enrichment


def test_keyword_only_ai_scores_far_below_implemented_ai():
    kw = "Fay\nfay@example.com\nSkills\nPython, LangChain, RAG, LangGraph\nProjects\nBlog | Django\n• Built a blog with Django.\n"
    s_kw, el = score(kw)
    s_deep, _ = score(DEEP)
    assert el.ai_level == "skills_only"
    assert s_kw.breakdown.ai_project_depth <= 6 < s_deep.breakdown.ai_project_depth

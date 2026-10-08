from helpers import DEEP, JS_ONLY, PY_NO_AI, THIN, analyse


def test_js_only_with_copilot_is_rejected_for_both_reasons():
    _, _, el = analyse(JS_ONLY)
    assert not el.eligible
    assert "No evidence of Python stack" in el.reasons
    assert "No AI/agentic project evidence" in el.reasons      # Copilot / AI-assisted dev != AI engineering


def test_python_without_ai_rejected():
    _, _, el = analyse(PY_NO_AI)
    assert not el.eligible and el.reasons == ["No AI/agentic project evidence"]


def test_deep_candidate_eligible_even_with_js_present():
    _, _, el = analyse(DEEP + "\nSkills\nJavaScript, React")
    assert el.eligible and el.ai_level == "implemented"


def test_thin_wrapper_is_still_eligible():
    _, _, el = analyse(THIN)
    assert el.eligible


def test_python_only_in_education_does_not_count():
    text = ("Eve Edu\neve@example.com\nEducation\nB.Tech CSE. Coursework: Python programming\n"
            "Projects\nBot | Node.js, OpenAI\n• Built a RAG chatbot with LangChain and Pinecone in Node.js.\n")
    _, _, el = analyse(text)
    assert not el.eligible and "Python only mentioned in education/coursework" in el.reasons


def test_ai_in_skills_only_is_eligible_but_flagged():
    text = "Fay Skills\nfay@example.com\nSkills\nPython, LangChain, RAG, FastAPI\nProjects\nBlog | Django\n• Built a blog with Django.\n"
    _, _, el = analyse(text)
    assert el.eligible and el.ai_level == "skills_only"


def test_classical_ml_only_rejected_by_default():
    text = "Gus Ml\ngus@example.com\nSkills\nPython, scikit-learn\nProjects\nChurn | sklearn\n• Built a churn model with scikit-learn and XGBoost.\n"
    _, _, el = analyse(text)
    assert not el.eligible and "classical" in el.reasons[0]

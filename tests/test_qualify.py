from gtm.models import Lead
from gtm.pipeline.qualify import heuristic_score


def test_heuristic_prefers_icp_title():
    icp = {
        "titles": ["VP of Sales", "Head of Growth"],
        "keywords_include": ["saas"],
        "keywords_exclude": ["intern"],
        "geos": ["United States"],
        "industries": ["SaaS"],
        "employee_min": 20,
        "employee_max": 200,
    }
    good = Lead(
        campaign_id=1,
        title="VP of Sales",
        company="Acme SaaS",
        industry="SaaS",
        location="United States",
        employee_count="80",
    )
    bad = Lead(
        campaign_id=1,
        title="Intern",
        company="Corner Cafe",
        industry="Food",
        location="United States",
        employee_count="4",
    )
    assert heuristic_score(good, icp) > 0.7
    assert heuristic_score(bad, icp) < 0.4

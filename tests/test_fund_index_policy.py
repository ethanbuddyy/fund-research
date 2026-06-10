from datetime import date

from src.collectors.fund_screener import apply_filters
from src.utils.fund_universe import is_index_fund


def test_known_active_funds_are_not_treated_as_index_funds():
    assert not is_index_fund(
        fund_code="270023",
        fund_type="主动QDII",
        fund_name="广发全球精选",
        benchmark="MSCI全球",
    )
    assert not is_index_fund(
        fund_code="164701",
        fund_type="LOF",
        fund_name="招商欧洲精选LOF",
        benchmark="MSCI欧洲",
    )


def test_index_lof_and_etf_link_are_allowed():
    assert is_index_fund(
        fund_code="161130",
        fund_type="LOF",
        fund_name="标普500指数LOF(富国)",
        benchmark="标普500",
    )
    assert is_index_fund(
        fund_type="ETF联接",
        fund_name="博时标普500ETF联接",
        benchmark="标普500",
    )


def test_screener_index_only_removes_active_candidates():
    candidates = [
        {
            "fund_code": "161130",
            "fund_name": "标普500指数LOF(富国)",
            "inception_date": "2020-01-01",
            "return_1y": 10.0,
            "return_3y": 20.0,
            "purchase_fee": 0.01,
        },
        {
            "fund_code": "999999",
            "fund_name": "全球成长精选混合",
            "inception_date": "2020-01-01",
            "return_1y": 30.0,
            "return_3y": 50.0,
            "purchase_fee": 0.01,
        },
    ]
    config = {
        "index_only": True,
        "min_inception_years": 2,
        "require_3y_record": False,
        "max_purchase_fee": 0.015,
        "min_aum_yi": 0,
    }

    kept = apply_filters(candidates, config, date(2026, 6, 9))
    assert [fund["fund_code"] for fund in kept] == ["161130"]

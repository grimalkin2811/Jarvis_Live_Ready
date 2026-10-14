from unittest.mock import Mock

import pytest

from src import forecast


@pytest.mark.parametrize("args", [{"city": ""}, {"city": []}, {"days": 0}, {"days": 8}, {"days": True}, {"days": "7"}])
def test_invalid_args_never_call_network(args, monkeypatch):
    fetch = Mock()
    monkeypatch.setattr(forecast, "_fetch_json", fetch)
    assert not forecast.get_forecast(**args)["success"]
    fetch.assert_not_called()


def test_forecast_and_provider_failures(monkeypatch):
    geo = {"results": [{"name": "Tours", "latitude": 47.4, "longitude": 0.7}]}
    payload = {"daily": {"time": ["2026-10-14"], "weather_code": [0], "temperature_2m_min": [10],
                         "temperature_2m_max": [18], "precipitation_probability_max": [5]}}
    fetch = Mock(side_effect=[geo, payload])
    monkeypatch.setattr(forecast, "_fetch_json", fetch)
    result = forecast.get_forecast("Tours", 7)
    assert result["success"] and result["partiel"] and result["count"] == 1
    assert result["jours"][0]["max_c"] == 18
    assert all(c.args[0].startswith("https://") for c in fetch.call_args_list)
    for results in ([{}], [geo, {}], [geo, {"daily": {"time": []}}], [geo, {"daily": {"time": ["bad-date"]}}]):
        fetch.side_effect = results
        assert not forecast.get_forecast()["success"]
    fetch.side_effect = OSError("offline")
    assert not forecast.get_forecast()["success"]

"""Unit tests for KorailService helpers that don't touch the network."""
import pytest

from services.korail_service import KorailService


@pytest.fixture
def service():
    return KorailService()


@pytest.mark.parametrize("train_str, expected", [
    ("[KTX] 9월 24일, 서울~동대구(18:13~20:07) 일반실 38,700원", 1813),
    ("[KTX] 4월 8일, 울산(통도사)~서울(09:44~12:50) 일반실", 944),
    ("[KTX] 4월 8일, 서울~부산(00:05~02:50) 예약 대기(일반)", 5),
    ("[KTX] 4월 8일, 판교(경기)~진부(오대산)(23:59~01:10)", 2359),
])
def test_extract_departure_time(service, train_str, expected):
    assert service._extract_departure_time(train_str) == expected


def test_extract_departure_time_returns_zero_when_missing(service):
    assert service._extract_departure_time("알 수 없는 형식") == 0


def test_filter_by_max_dep_time_keeps_trains_strictly_before_max(service):
    trains = [
        "[KTX] 서울~동대구(17:00~18:50)",
        "[KTX] 서울~동대구(19:59~21:40)",
        "[KTX] 서울~동대구(20:00~21:45)",
        "알 수 없는 형식",
    ]
    assert service._filter_by_max_dep_time(trains, 2000, verbose=False) == trains[:2]

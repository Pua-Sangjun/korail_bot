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


class FakeTrain:
    def __init__(self, general=False, special=False):
        self.general, self.special = general, special

    def has_general_seat(self):
        return self.general

    def has_special_seat(self):
        return self.special

    def has_seat(self):
        return self.general or self.special


class FakeKorail:
    """Returns one search result per call, then empty lists."""

    def __init__(self, results):
        self.results = list(results)
        self.reserve_calls = 0

    def search_train(self, *args, **kwargs):
        return self.results.pop(0) if self.results else []

    def reserve(self, *args, **kwargs):
        self.reserve_calls += 1


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr("services.korail_service.time.sleep", lambda _: None)


def test_search_trains_requires_a_session(service):
    with pytest.raises(ValueError):
        service.search_trains("20261030", "서울", "부산")


def test_start_without_login_allows_search_but_not_reserve(service):
    service.start_without_login()
    service._korail_instance = FakeKorail([[FakeTrain(general=True)]])
    assert len(service.search_trains("20261030", "서울", "부산")) == 1
    with pytest.raises(ValueError):
        service.reserve_train(FakeTrain(general=True))


@pytest.mark.parametrize("option, train, expected", [
    ("GENERAL_FIRST", FakeTrain(special=True), True),
    ("SPECIAL_FIRST", FakeTrain(general=True), True),
    ("GENERAL_ONLY", FakeTrain(special=True), False),
    ("GENERAL_ONLY", FakeTrain(general=True), True),
    ("SPECIAL_ONLY", FakeTrain(general=True), False),
    ("SPECIAL_ONLY", FakeTrain(special=True), True),
])
def test_has_wanted_seat(option, train, expected):
    from services.korail_service import _has_wanted_seat
    assert _has_wanted_seat(train, option) is expected


def test_wait_for_seats_skips_wrong_seat_class_and_never_reserves(service, no_sleep):
    general_only = FakeTrain(general=True)
    special = FakeTrain(special=True)
    fake = FakeKorail([[], [general_only], [general_only, special]])
    service.start_without_login()
    service._korail_instance = fake

    found = service.wait_for_seats("20261030", "서울", "부산", reserve_option="SPECIAL_ONLY")

    assert found == [special]
    assert fake.results == []
    assert fake.reserve_calls == 0


def test_wait_for_seats_stops_at_max_attempts(service, no_sleep):
    service.start_without_login()
    service._korail_instance = FakeKorail([])
    assert service.wait_for_seats("20261030", "서울", "부산", max_attempts=3) == []


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


BLOCKED_BODY = {"code": -2000, "message": "매크로 등 미허가 도구 사용 시 이용이 제한될 수 있습니다."}


def test_raise_if_blocked_detects_macro_block():
    from services.korail_service import _raise_if_blocked, BlockedByKorailError
    with pytest.raises(BlockedByKorailError, match="매크로"):
        _raise_if_blocked(FakeResponse(403, BLOCKED_BODY))


@pytest.mark.parametrize("response", [
    FakeResponse(200, {"strResult": "SUCC"}),
    FakeResponse(403, ValueError("not json")),
    FakeResponse(403, {"code": -1}),
])
def test_raise_if_blocked_ignores_other_responses(response):
    from services.korail_service import _raise_if_blocked
    _raise_if_blocked(response)


def test_wait_for_seats_stops_when_blocked(service, no_sleep):
    from services.korail_service import BlockedByKorailError

    class BlockedKorail:
        def search_train(self, *args, **kwargs):
            raise BlockedByKorailError("blocked")

    service.start_without_login()
    service._korail_instance = BlockedKorail()
    with pytest.raises(BlockedByKorailError):
        service.wait_for_seats("20261030", "서울", "부산")

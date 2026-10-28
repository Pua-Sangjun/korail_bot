"""
Terminal entry point for Korail KTX reservation.

Takes the search conditions in the terminal, then searches until a seat is
found, printing progress along the way. Depending on the mode it either
reserves the seat or only alerts so you can book it yourself.

Usage:
    python src/cli.py
"""
import getpass
import logging
import os
import sys
import time

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(os.path.dirname(SRC_DIR), ".env")


def load_env_file(path: str) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing values."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# config.settings reads os.environ at import time, so .env must be loaded first.
load_env_file(ENV_FILE)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from korail2 import TrainType, ReserveOption  # noqa: E402

from config.settings import settings  # noqa: E402
from services.korail_service import (  # noqa: E402
    BlockedByKorailError,
    DuplicateReservationError,
    KorailService,
)
from utils.notifier import notify  # noqa: E402
from utils.validators import InputValidator  # noqa: E402

PROGRESS_LOG_INTERVAL = 40  # attempts, roughly once a minute at the default search interval

MODE_RESERVE = "1"
MODE_NOTIFY_ONLY = "2"

TRAIN_TYPES = {"1": (TrainType.KTX, "KTX만"), "2": (TrainType.ALL, "모든 열차")}
SEAT_OPTIONS = {
    "1": (ReserveOption.GENERAL_FIRST, "일반실 우선"),
    "2": (ReserveOption.GENERAL_ONLY, "일반실만"),
    "3": (ReserveOption.SPECIAL_FIRST, "특실 우선"),
    "4": (ReserveOption.SPECIAL_ONLY, "특실만"),
}
SEAT_STRATEGIES = {"1": ("consecutive", "연속 좌석"), "2": ("random", "랜덤 배치")}


def ask(prompt: str, validate) -> str:
    """Prompt until validate(answer) returns (True, None)."""
    while True:
        answer = input(f"{prompt}: ").strip()
        is_valid, error = validate(answer)
        if is_valid:
            return answer
        print(f"  ⚠️  {error}")


def validate_max_time(text: str):
    if text == "2400":
        return True, None
    return InputValidator.validate_time(text)


def validate_not_empty(text: str):
    return (True, None) if text else (False, "값을 입력해주세요.")


def validate_mode(text: str):
    if text in (MODE_RESERVE, MODE_NOTIFY_ONLY):
        return True, None
    return False, "1 또는 2를 입력해주세요."


def ask_mode() -> str:
    print("━━━━━━━━━━ 모드 선택 ━━━━━━━━━━")
    print("  1: 자리가 나면 자동 예약 (코레일 로그인 필요)")
    print("  2: 자리가 나면 알림만 (로그인 없이 조회, 예약은 직접)")
    mode = ask("모드", validate_mode)
    print()
    return mode


def login() -> KorailService:
    korail = KorailService()
    username = settings.KORAIL_USER_ID
    password = settings.KORAIL_PASSWORD

    if username and password:
        print(f"🔐 .env에 저장된 계정({username})으로 로그인합니다...")
        if korail.login(username, password):
            print("✅ 로그인 성공\n")
            return korail
        print("❌ .env 계정으로 로그인하지 못했습니다. 직접 입력해주세요.\n")

    while True:
        username = ask("코레일 아이디 (회원번호, 010-xxxx-xxxx, 이메일)", validate_not_empty)
        password = getpass.getpass("코레일 비밀번호 (입력해도 화면에 표시되지 않아요): ")
        if korail.login(username, password):
            print("✅ 로그인 성공\n")
            return korail
        print("❌ 로그인 실패. 아이디와 비밀번호를 다시 확인해주세요.\n")


def ask_search_conditions(mode: str) -> dict:
    print("━━━━━━━━━━ 예약 조건 입력 ━━━━━━━━━━")
    dep_date = ask("출발일 (예: 20261030)", InputValidator.validate_date)
    src = ask("출발역 ('역' 제외, 예: 서울)", InputValidator.validate_station_name)
    dst = ask("도착역 ('역' 제외, 예: 부산)", InputValidator.validate_station_name)
    dep_time = ask("검색 시작 시각 (HHMM, 예: 0800)", InputValidator.validate_time)
    max_dep_time = ask("검색 최대 시각 (HHMM, 2400 = 제한 없음)", validate_max_time)
    train_choice = ask("열차 종류 (1: KTX만, 2: 모든 열차)", InputValidator.validate_train_type_choice)
    seat_choice = ask(
        "좌석 (1: 일반실 우선, 2: 일반실만, 3: 특실 우선, 4: 특실만)",
        InputValidator.validate_special_option_choice,
    )
    passenger_count = int(ask("탑승 인원 (1~9)", InputValidator.validate_passenger_count))

    strategy_choice = "1"
    if passenger_count > 1 and mode == MODE_RESERVE:
        strategy_choice = ask(
            "좌석 배치 (1: 연속 좌석, 2: 랜덤 배치 - 한 자리씩 따로 잡음)",
            InputValidator.validate_seat_strategy_choice,
        )

    train_type, train_label = TRAIN_TYPES[train_choice]
    reserve_option, seat_label = SEAT_OPTIONS[seat_choice]
    seat_strategy, strategy_label = SEAT_STRATEGIES[strategy_choice]
    max_time_label = "제한 없음" if max_dep_time == "2400" else f"{max_dep_time[:2]}:{max_dep_time[2:]} 전"
    show_strategy = passenger_count > 1 and mode == MODE_RESERVE
    passenger_label = f"{passenger_count}명" + (f" ({strategy_label})" if show_strategy else "")
    mode_label = "자동 예약" if mode == MODE_RESERVE else "알림만"

    return {
        "dep_date": dep_date,
        "src_locate": src,
        "dst_locate": dst,
        "dep_time": dep_time + "00",
        "max_dep_time": max_dep_time,
        "train_type": train_type,
        "reserve_option": reserve_option,
        "passenger_count": passenger_count,
        "seat_strategy": seat_strategy,
        "_summary": [
            ("모드", mode_label),
            ("출발일", f"{dep_date[:4]}-{dep_date[4:6]}-{dep_date[6:]}"),
            ("구간", f"{src} → {dst}"),
            ("출발 시각", f"{dep_time[:2]}:{dep_time[2:]} ~ {max_time_label}"),
            ("열차", train_label),
            ("좌석", seat_label),
            ("인원", passenger_label),
        ],
    }


def confirm(conditions: dict) -> bool:
    print("\n━━━━━━━━━━ 입력한 조건 ━━━━━━━━━━")
    for label, value in conditions["_summary"]:
        print(f"  {label:<6} {value}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    while True:
        is_yes, error = InputValidator.validate_yes_no(input("이 조건으로 조회를 시작할까요? (Y/N): "))
        if is_yes is not None:
            return is_yes
        print(f"  ⚠️  {error}")


def search_params(conditions: dict) -> dict:
    return {k: v for k, v in conditions.items() if not k.startswith("_")}


def run_search(korail: KorailService, conditions: dict):
    params = search_params(conditions)
    duplicate_warned = False
    while True:
        try:
            return korail.search_and_reserve_loop(**params)
        except DuplicateReservationError:
            if not duplicate_warned:
                print("\n⚠️  같은 열차에 이미 예약이 있어요. 기존 예약이 취소될 때까지 10초마다 다시 시도합니다.")
                print(f"   기존 예약 확인: {settings.KORAIL_PAYMENT_URL}\n")
                duplicate_warned = True
            time.sleep(10)


def print_success(reservation) -> None:
    reservations = getattr(reservation, "_all_reservations", [reservation])
    print("\n" + "🎉" * 15)
    print("  열차 예약에 성공했습니다!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for i, res in enumerate(reservations, 1):
        prefix = f"  좌석 {i}: " if len(reservations) > 1 else "  "
        print(f"{prefix}{res}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  ⚠️  {settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제하지 않으면 예약이 취소됩니다!")
    print("  코레일톡 앱 또는 아래 링크에서 결제하세요.")
    print(f"  🔗 {settings.KORAIL_PAYMENT_URL}")
    print("🎉" * 15 + "\n")
    notify(
        "🎉 KTX 예약 성공",
        f"{settings.PAYMENT_TIMEOUT_MINUTES}분 안에 결제하세요! {reservations[0]}",
        click_url=settings.KORAIL_PAYMENT_URL,
    )


def run_watch(korail: KorailService, conditions: dict) -> list:
    params = search_params(conditions)
    params.pop("seat_strategy")
    return korail.wait_for_seats(**params)


def print_available(trains: list) -> None:
    print("\n" + "🔔" * 15)
    print("  자리가 있는 열차를 찾았습니다!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for train in trains:
        print(f"  {train}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  예약은 하지 않았어요. 금방 다시 매진될 수 있으니 바로 예매하세요.")
    print("  코레일톡 앱 또는 🔗 https://www.letskorail.com")
    print("🔔" * 15 + "\n")
    extra = f" 외 {len(trains) - 1}개" if len(trains) > 1 else ""
    notify("🔔 KTX 자리 있음", f"{trains[0]}{extra} - 지금 바로 예매하세요!", click_url="https://www.letskorail.com")


def main() -> int:
    print("🚄 코레일 KTX 자동 예매 (터미널 모드)\n")
    try:
        mode = ask_mode()
        if mode == MODE_RESERVE:
            korail = login()
        else:
            korail = KorailService()
            korail.start_without_login()
        korail.progress_log_interval = PROGRESS_LOG_INTERVAL

        conditions = ask_search_conditions(mode)
        if not confirm(conditions):
            print("취소했습니다.")
            return 0

        print("\n🔍 좌석 조회를 시작합니다. 약 1분마다 진행 상황이 표시돼요.")
        print("   중단하려면 Ctrl + C 를 누르세요.\n")
        if mode == MODE_NOTIFY_ONLY:
            trains = run_watch(korail, conditions)
        else:
            reservation = run_search(korail, conditions)
    except KeyboardInterrupt:
        print("\n\n⏹  조회를 중단했습니다.")
        return 130
    except BlockedByKorailError as e:
        print("\n⛔ 코레일이 이 프로그램의 접속을 막았습니다. 계속 시도하면 계정이 제한될 수 있어 중단합니다.")
        print(f"   코레일 메시지: {e}")
        print("   코레일톡 앱의 예약대기 기능이나 https://www.letskorail.com 을 이용해주세요.")
        return 2

    if mode == MODE_NOTIFY_ONLY:
        if not trains:
            print("❌ 자리가 있는 열차를 찾지 못했습니다.")
            return 1
        print_available(trains)
        return 0

    if not reservation:
        print("❌ 예약 결과를 받지 못했습니다. 코레일 사이트에서 예약 내역을 확인해주세요.")
        return 1

    print_success(reservation)
    return 0


if __name__ == "__main__":
    sys.exit(main())

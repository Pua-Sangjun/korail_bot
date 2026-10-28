"""Korail API service wrapper."""
import re
import time
from typing import Optional, List
import requests
from korail2 import (
    Korail as K2MKorail, TrainType, ReserveOption, SoldOutError, NoResultsError,
    NeedToLoginError, AdultPassenger
)

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

# korail2 sends requests without a timeout, so a dropped connection blocks the search loop forever.
REQUEST_TIMEOUT_SECONDS = 10

# Attempts between verbose (debug) search logs.
SUMMARY_LOG_INTERVAL = 60

DUPLICATE = "DUPLICATE"

_TRAIN_TIME_RANGE = re.compile(r"\((\d{2}):(\d{2})~\d{2}:\d{2}\)")


class DuplicateReservationError(Exception):
    """Raised when attempting to reserve a train that's already reserved."""


class _TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault('timeout', REQUEST_TIMEOUT_SECONDS)
        return super().request(method, url, **kwargs)


def _create_korail(username: str, password: str) -> K2MKorail:
    korail = K2MKorail(username, password, auto_login=False)
    session = _TimeoutSession()
    session.headers.update(K2MKorail._session.headers)
    korail._session = session
    return korail


class KorailService:
    """Service for interacting with Korail API."""

    def __init__(self):
        """Initialize Korail service."""
        self._korail_instance: Optional[K2MKorail] = None
        self._logged_in = False
        self._search_interval = settings.KORAIL_SEARCH_INTERVAL
        self._username: Optional[str] = None
        self._password: Optional[str] = None
        self._last_login_time: float = 0
        self._relogin_interval: int = 30 * 60  # 30 minutes
        self._relogin_count: int = 0
        self.progress_log_interval: int = 1000  # attempts between "still searching" logs

    def login(self, username: str, password: str) -> bool:
        """
        Login to Korail with credentials.

        Args:
            username: Korail username (phone number in format 010-xxxx-xxxx)
            password: Korail password

        Returns:
            True if login successful, False otherwise
        """
        try:
            self._korail_instance = _create_korail(username, password)
            self._logged_in = self._korail_instance.login()

            if self._logged_in:
                self._username = username
                self._password = password
                self._last_login_time = time.time()
                logger.info(f"Korail login successful for user: {username}")
            else:
                logger.warning(f"Korail login failed for user: {username}")

            return self._logged_in
        except Exception as e:
            logger.error(f"Korail login error for user {username}: {e}")
            return False

    def _relogin(self) -> bool:
        """Attempt to re-login with stored credentials after session expiry."""
        if not self._username or not self._password:
            logger.error("🔒 Cannot re-login: no stored credentials")
            return False

        logger.debug("🔄 Session expired, attempting re-login...")
        try:
            self._korail_instance = _create_korail(self._username, self._password)
            self._logged_in = self._korail_instance.login()
            if self._logged_in:
                self._last_login_time = time.time()
                self._relogin_count += 1
                logger.debug(f"✅ Re-login successful (total: {self._relogin_count})")
            else:
                logger.error("❌ Re-login failed")
            return self._logged_in
        except Exception as e:
            logger.error(f"❌ Re-login error: {e}")
            self._logged_in = False
            return False

    def _check_session_refresh(self):
        """Proactively re-login if session is older than the relogin interval."""
        if self._last_login_time and (time.time() - self._last_login_time) >= self._relogin_interval:
            logger.debug(f"🔄 Session older than {self._relogin_interval}s, proactive re-login")
            self._relogin()

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        passenger_count: int = 1,
        verbose: bool = True
    ) -> List:
        """
        Search for available trains.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station name (without '역')
            dst_locate: Destination station name (without '역')
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time threshold (HHMM)
            train_type: Type of train to search for
            passenger_count: Number of adult passengers

        Returns:
            List of available trains

        Raises:
            ValueError: If not logged in
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before searching trains")

        if verbose:
            logger.debug(
                f"🔍 Searching trains: {src_locate}→{dst_locate} on {dep_date} from {dep_time} "
                f"(max {max_dep_time}, {train_type}, passengers={passenger_count})"
            )

        try:
            trains = self._korail_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time,
                train_type=train_type,
                passengers=[AdultPassenger(passenger_count)]
            )

            if verbose:
                logger.debug(f"📋 Korail API returned {len(trains) if trains else 0} trains")
                for i, train in enumerate(trains or [], 1):
                    logger.debug(f"  Train #{i}: {train}")

            if trains and max_dep_time != "2400":
                trains = self._filter_by_max_dep_time(trains, int(max_dep_time), verbose)

            if verbose:
                logger.debug(f"✅ Search complete: {len(trains)} trains available")
            return trains

        except NoResultsError:
            if verbose:
                logger.debug("No trains found for search criteria (NoResultsError)")
            return []
        except requests.RequestException as e:
            logger.warning(f"⚠️ Network error while searching trains, retrying: {e}")
            return []
        except NeedToLoginError as e:
            logger.debug(f"🔒 Session expired during search, re-logging in: {e}")
            if self._relogin():
                return []  # Will retry on next loop iteration
            raise
        except Exception as e:
            logger.error(f"❌ Error searching trains: {e}", exc_info=True)
            return []

    def _filter_by_max_dep_time(self, trains: List, max_time: int, verbose: bool) -> List:
        """Keep only trains departing strictly before max_time (HHMM)."""
        kept = []
        for train in trains:
            dep_time = self._extract_departure_time(train)
            if 0 < dep_time < max_time:
                kept.append(train)
                if verbose:
                    logger.debug(f"  ✅ Kept: {dep_time} < {max_time}")
            elif verbose:
                logger.debug(f"  ❌ Filtered out: {dep_time} >= {max_time}")
        if verbose:
            logger.debug(f"📊 After max_dep_time filter ({max_time}): {len(kept)} trains remain")
        return kept

    def reserve_train(
        self,
        train,
        option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1
    ):
        """
        Attempt to reserve a specific train.

        Args:
            train: Train object from search_trains()
            option: Reservation option (special seat preference)
            passenger_count: Number of adult passengers

        Returns:
            Reservation object if successful, None otherwise
            Returns DUPLICATE if a duplicate reservation is detected
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before reserving")

        logger.debug(f"🎫 Attempting reservation: {train} (option={option}, passengers={passenger_count})")
        try:
            reservation = self._korail_instance.reserve(
                train, passengers=[AdultPassenger(passenger_count)], option=option
            )
        except SoldOutError:
            logger.debug(f"Train sold out during reservation attempt: {train}")
            return None
        except NeedToLoginError as e:
            logger.debug(f"🔒 Session expired during reservation, re-logging in: {e}")
            if self._relogin():
                return None  # Will retry on next loop iteration
            raise
        except Exception as e:
            error_msg = str(e)
            if "동일한 예약 내역" in error_msg or "WRR800029" in error_msg:
                logger.warning(f"⚠️ Duplicate reservation detected: {error_msg}")
                return DUPLICATE

            logger.error(
                f"❌ Reservation error ({type(e).__name__}): {error_msg} "
                f"(train={train}, option={option})",
                exc_info=True
            )
            return None

        if not reservation:
            logger.debug("Reservation returned None (no seats available)")
            return None

        logger.info(f"🎉 RESERVATION SUCCESS! {reservation}")
        if hasattr(reservation, 'rsv_id'):
            logger.info(f"  Reservation ID: {reservation.rsv_id}")
        return reservation

    def search_and_reserve_loop(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        reserve_option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1,
        seat_strategy: str = "consecutive",
        max_attempts: Optional[int] = None
    ):
        """
        Continuously search for trains and attempt reservation until successful.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station
            dst_locate: Destination station
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time (HHMM)
            train_type: Train type filter
            reserve_option: Reservation option
            passenger_count: Number of adult passengers
            seat_strategy: "consecutive" for seats together, "random" for separate seats
            max_attempts: Maximum attempts (None for infinite)

        Returns:
            Reservation object(s) when successful, None if max_attempts reached

        Raises:
            DuplicateReservationError: As soon as Korail reports a duplicate reservation
        """
        if not self._logged_in:
            raise ValueError("Must login before searching")

        logger.info(
            f"Starting reservation loop: {src_locate} -> {dst_locate} "
            f"on {dep_date} at {dep_time} for {passenger_count} passengers ({seat_strategy} seating)"
        )

        loop = (
            self._search_and_reserve_consecutive if seat_strategy == "consecutive"
            else self._search_and_reserve_random
        )
        return loop(
            dep_date, src_locate, dst_locate, dep_time, max_dep_time,
            train_type, reserve_option, passenger_count, max_attempts
        )

    def _search_and_reserve_consecutive(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int]
    ):
        """Reserve seats consecutively (together)."""
        logger.info(f"🔄 Starting consecutive seat search loop (passengers={passenger_count})")

        attempts = 0
        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"❌ Reached max attempts ({max_attempts}), stopping")
                return None

            if attempts % self.progress_log_interval == 0:
                logger.info(f"📊 Search attempt #{attempts} (still searching..., re-logins: {self._relogin_count})")

            trains = self._search_attempt(
                attempts, dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count
            )
            if not trains:
                continue

            for idx, train in enumerate(trains, 1):
                logger.debug(f"🚂 Trying train {idx}/{len(trains)}")
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=passenger_count)
                _raise_if_duplicate(reservation)
                if reservation:
                    logger.info(f"🎉 CONSECUTIVE RESERVATION SUCCESS after {attempts} attempts!")
                    return reservation

            logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")
            time.sleep(self._search_interval)

    def _search_and_reserve_random(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int]
    ):
        """Reserve seats randomly (one at a time until target count reached)."""
        reservations = []
        logger.info(f"Random seating: will reserve {passenger_count} individual tickets")

        attempts = 0
        while len(reservations) < passenger_count:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"Reached max attempts ({max_attempts}), stopping")
                if reservations:
                    logger.warning(f"{len(reservations)} partial reservations were not cancelled: {reservations}")
                return None

            if attempts % self.progress_log_interval == 0:
                logger.info(
                    f"📊 Search attempt #{attempts} (still searching..., "
                    f"{len(reservations)}/{passenger_count} reserved, re-logins: {self._relogin_count})"
                )

            trains = self._search_attempt(
                attempts, dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count=1
            )
            if not trains:
                continue

            for train in trains:
                logger.debug(f"Trying train {train} for seat {len(reservations) + 1}/{passenger_count}")
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=1)
                _raise_if_duplicate(reservation)
                if not reservation:
                    continue

                reservations.append(reservation)
                logger.info(f"Reserved seat {len(reservations)}/{passenger_count} (attempt #{attempts})")

                if len(reservations) >= passenger_count:
                    logger.info(f"All {passenger_count} seats reserved successfully! Total attempts: {attempts}")
                    # cli.py reads every seat from _all_reservations on the first reservation.
                    first_reservation = reservations[0]
                    first_reservation._all_reservations = reservations
                    first_reservation._is_random_allocation = True
                    first_reservation._total_seats = passenger_count
                    return first_reservation

                # Longer pause between individual reservations to avoid rate limiting.
                time.sleep(self._search_interval * 1.5)
                break

            time.sleep(self._search_interval)

        return reservations[0] if reservations else None

    def _search_attempt(
        self,
        attempt: int,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        passenger_count: int
    ) -> List:
        """Run one loop search; sleeps for the search interval when nothing is found."""
        verbose = attempt % SUMMARY_LOG_INTERVAL == 0
        if verbose:
            logger.debug(f"━━━ Search attempt #{attempt} ━━━")

        self._check_session_refresh()
        trains = self.search_trains(
            dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count,
            verbose=verbose
        )

        if not trains:
            if verbose:
                logger.debug(f"📊 Attempt #{attempt}: no trains found, retrying...")
            time.sleep(self._search_interval)
        return trains

    def _extract_departure_time(self, train) -> int:
        """
        Extract departure time from train object as HHMM integer.

        Args:
            train: Train object from korail2

        Returns:
            Departure time as integer (e.g., 944 for 09:44), 0 if extraction fails
        """
        # str(train): "[KTX] 4월 8일, 울산(통도사)~서울(09:44~12:50) 일반실 ..." — station names and
        # trailing labels may contain parentheses, so match the time range itself.
        match = _TRAIN_TIME_RANGE.search(str(train))
        if not match:
            logger.error(f"Failed to extract departure time from train: {train}")
            return 0
        return int(match.group(1) + match.group(2))

    @property
    def is_logged_in(self) -> bool:
        """Check if currently logged in."""
        return self._logged_in


def _raise_if_duplicate(reservation) -> None:
    if reservation == DUPLICATE:
        logger.warning("⚠️ Duplicate reservation detected - notifying user")
        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")

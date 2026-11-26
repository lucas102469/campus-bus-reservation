from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from pathlib import Path
from datetime import datetime, date, time, timedelta

app = Flask(__name__)
app.secret_key = "change-this-secret-key"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "shuttle.db"

CAPACITY_PER_BUS = 45
CANCELLATION_LIMIT_MINUTES = 10

DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]

# 등교 배차
SCHEDULES_GO = {
    "양주역": [
        {"time": "08:00", "buses": [1]},
        {"time": "08:45", "buses": [1]},
        {"time": "08:50", "buses": [1]},
        {"time": "09:10", "buses": [1]},
        {"time": "09:40", "buses": [1]},
        {"time": "09:50", "buses": [1]},
        {"time": "10:30", "buses": [1]},
        {"time": "11:10", "buses": [1]},
    ],
    "도봉산역": [
        {"time": "08:50", "buses": [1, 2, 3, 4]},
        {"time": "09:50", "buses": [1, 2, 3]},
        {"time": "10:50", "buses": [1, 2]},
    ],
    "의정부 중앙역": [
        {"time": "08:50", "buses": [1]},
        {"time": "09:50", "buses": [1]},
    ],
}

# 하교 배차
SCHEDULES_RETURN = {
    "양주역": [
        {"time": "13:30", "buses": [1]},
        {"time": "14:00", "buses": [1]},
        {"time": "14:30", "buses": [1]},
        {"time": "15:30", "buses": [1]},
        {"time": "16:10", "buses": [1]},
        {"time": "17:00", "buses": [1]},
        {"time": "17:40", "buses": [1]},
        {"time": "17:45", "buses": [1]},
        {"time": "18:30", "buses": [1]},
    ],
    "도봉산역": [
        {"time": "16:30", "buses": [1]},
        {"time": "17:30", "buses": [1]},
        {"time": "18:30", "buses": [1]},
    ],
    "의정부 중앙역": [
        {"time": "14:00", "buses": [1]},
        {"time": "15:00", "buses": [1]},
    ],
}

# 월=0, 화=1, 수=2, 목=3, 금=4
RETURN_WEEKDAYS = {
    "양주역": {
        "13:30": {0, 1, 2, 3},
        "14:00": {0, 1, 2, 3},
        "14:30": {0, 1, 2, 3},
        "15:30": {0, 1, 2, 3},
        "16:10": {0, 1, 2, 3},
        "17:00": {0, 1, 2, 3},
        "17:40": {4},
        "17:45": {0, 1, 2, 3},
        "18:30": {0, 1, 2, 3, 4},
    },
    "도봉산역": {
        "16:30": {0, 1, 2, 3},
        "17:30": {0, 1, 2, 3, 4},
        "18:30": {0, 1, 2, 3},
    },
    "의정부 중앙역": {
        "14:00": {0, 1, 2, 3},
        "15:00": {0, 1, 2, 3},
    },
}

GO_WEEKDAYS = {
    "양주역": {
        "08:00": {0, 1, 2, 3},
        "08:45": {4},
        "08:50": {0, 1, 2, 3},
        "09:10": {0, 1, 2, 3},
        "09:40": {4},
        "09:50": {0, 1, 2, 3},
        "10:30": {0, 1, 2, 3},
        "11:10": {0, 1, 2, 3},
    },
    "도봉산역": {
        "08:50": {0, 1, 2, 3, 4},
        "09:50": {0, 1, 2, 3},
        "10:50": {0, 1, 2, 3},
    },
    "의정부 중앙역": {
        "08:50": {0, 1, 2, 3},
        "09:50": {0, 1, 2, 3},
    },
}


# SQLite 데이터베이스 연결을 생성하고 행을 딕셔너리처럼 사용할 수 있도록 설정한다.
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# 예약 정보를 저장할 테이블과 좌석 중복 방지용 인덱스를 처음 실행할 때 생성한다.
def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            reservation_date TEXT NOT NULL,
            station TEXT NOT NULL,
            direction TEXT NOT NULL,
            departure_time TEXT NOT NULL,
            bus_number INTEGER NOT NULL,
            seat_number INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(student_id, reservation_date, direction)
        )
    """)

    # 같은 배차의 같은 버스·좌석이 중복 저장되지 않도록 DB에서도 보장한다.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_reservation_seat
        ON reservations (
            reservation_date,
            station,
            direction,
            departure_time,
            bus_number,
            seat_number
        )
    """)

    conn.commit()
    conn.close()


# 선택한 날짜, 정류장, 등교/하교 방향에 맞는 실제 운행 시간표만 반환한다.
def get_schedules(direction, station, selected_date):
    if selected_date.weekday() >= 5:
        return []

    if direction == "등교":
        source = SCHEDULES_GO
        weekday_map = GO_WEEKDAYS
    elif direction == "하교":
        source = SCHEDULES_RETURN
        weekday_map = RETURN_WEEKDAYS
    else:
        return []

    result = []

    for item in source.get(station, []):
        if selected_date.weekday() in weekday_map.get(station, {}).get(item["time"], set()):
            result.append({
                "time": item["time"],
                "buses": item["buses"].copy()
            })

    return result


# 특정 날짜와 시간대에 현재 몇 명이 예약했는지 조회한다.
def get_reservation_count(conn, reservation_date, station, direction, departure_time):
    row = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM reservations
        WHERE reservation_date = ?
          AND station = ?
          AND direction = ?
          AND departure_time = ?
    """, (
        reservation_date,
        station,
        direction,
        departure_time
    )).fetchone()

    return row["cnt"]


# 운행 중인 버스들을 순서대로 확인하여 비어 있는 좌석을 자동 배정한다.
def find_available_bus_and_seat(
    conn,
    reservation_date,
    station,
    direction,
    departure_time,
    buses
):
    for bus_number in buses:
        row = conn.execute("""
            SELECT seat_number
            FROM reservations
            WHERE reservation_date = ?
              AND station = ?
              AND direction = ?
              AND departure_time = ?
              AND bus_number = ?
            ORDER BY seat_number
        """, (
            reservation_date,
            station,
            direction,
            departure_time,
            bus_number
        )).fetchall()

        used = {r["seat_number"] for r in row}

        for seat_number in range(1, CAPACITY_PER_BUS + 1):
            if seat_number not in used:
                return bus_number, seat_number

    return None, None


# 문자열로 받은 날짜와 시간을 실제 datetime 값으로 변환한다.
def get_departure_datetime(reservation_date, departure_time):
    return datetime.combine(
        datetime.strptime(reservation_date, "%Y-%m-%d").date(),
        datetime.strptime(departure_time, "%H:%M").time()
    )


@app.context_processor
# 모든 HTML 페이지에서 로그인 여부와 현재 학번을 사용할 수 있도록 전달한다.
def inject_common():
    return {
        "logged_in": "student_id" in session,
        "student_id": session.get("student_id")
    }


@app.route("/")
# 로그인한 사용자가 정류장과 등교/하교를 선택하는 메인 화면을 보여준다.
def index():
    if "student_id" not in session:
        return redirect(url_for("login"))

    return render_template("index.html")


@app.route("/timetable")
# 정류장별 전체 셔틀버스 운행 시간표를 보여준다.
def timetable():
    if "student_id" not in session:
        return redirect(url_for("login"))

    return render_template("timetable.html")


@app.route("/login", methods=["GET", "POST"])
# 학번을 확인하여 로그인시키고, 등록되지 않은 학번은 거부한다.
def login():
    if "student_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()

        if student_id == "2221054":
            session["student_id"] = student_id
            return redirect(url_for("index"))

        flash("등록된 학번이 아닙니다.")

    return render_template("login.html")


@app.route("/logout")
# 현재 로그인 세션을 삭제하여 로그아웃 처리한다.
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/reserve/<direction>/<station>")
# 선택한 정류장과 방향의 날짜별 운행 시간 및 잔여 좌석을 화면에 보여준다.
def reserve(direction, station):
    if direction not in ("등교", "하교"):
        flash("잘못된 예약 방향입니다.")
        return redirect(url_for("index"))

    if station not in SCHEDULES_GO:
        flash("존재하지 않는 정류장입니다.")
        return redirect(url_for("index"))

    if "student_id" not in session:
        flash("로그인을 먼저 해야 합니다.")
        return redirect(url_for("login"))

    selected_date_text = request.args.get("date")

    try:
        selected_date = (
            datetime.strptime(selected_date_text, "%Y-%m-%d").date()
            if selected_date_text
            else date.today()
        )
    except ValueError:
        selected_date = date.today()

    schedules = get_schedules(direction, station, selected_date)

    # 이미 출발 시간이 지난 배차는 화면에서 예약할 수 없도록 표시한다.
    now = datetime.now()
    for item in schedules:
        departure_datetime = get_departure_datetime(
            selected_date.isoformat(),
            item["time"]
        )
        item["is_past"] = departure_datetime < now

    conn = get_db()

    for item in schedules:
        total_capacity = len(item["buses"]) * CAPACITY_PER_BUS
        reserved_count = get_reservation_count(
            conn,
            selected_date.isoformat(),
            station,
            direction,
            item["time"]
        )

        item["capacity"] = total_capacity
        item["reserved"] = reserved_count
        item["remaining"] = total_capacity - reserved_count

    conn.close()

    return render_template(
        "reserve.html",
        station=station,
        direction=direction,
        selected_date=selected_date.isoformat(),
        selected_date_display=selected_date.strftime("%Y년 %m월 %d일"),
        day_name=DAY_NAMES[selected_date.weekday()],
        schedules=schedules,
        today=date.today().isoformat()
    )


@app.route("/reserve/<direction>/<station>/submit", methods=["POST"])
# 사용자가 선택한 시간대의 예약 가능 여부를 검증하고 좌석을 자동 배정하여 저장한다.
def reserve_submit(direction, station):
    # URL로 전달된 정류장과 방향이 실제 서비스에서 사용하는 값인지 확인한다.
    if direction not in ("등교", "하교") or station not in SCHEDULES_GO:
        flash("잘못된 예약 요청입니다.")
        return redirect(url_for("index"))

    # 로그인한 학생만 실제 예약 처리를 할 수 있다.
    if "student_id" not in session:
        flash("로그인을 먼저 해야 합니다.")
        return redirect(url_for("login"))

    student_id = session["student_id"]
    reservation_date = request.form.get("reservation_date", "")
    departure_time = request.form.get("departure_time", "")

    try:
        selected_date = datetime.strptime(
            reservation_date,
            "%Y-%m-%d"
        ).date()
    except ValueError:
        flash("예약 날짜가 올바르지 않습니다.")
        return redirect(url_for(
            "reserve",
            direction=direction,
            station=station
        ))

    # 서버에서 실제 운행 배차인지 다시 확인한다.
    schedules = get_schedules(direction, station, selected_date)
    target = next(
        (s for s in schedules if s["time"] == departure_time),
        None
    )

    if target is None:
        flash("해당 날짜에는 운행하지 않는 배차입니다.")
        return redirect(url_for(
            "reserve",
            direction=direction,
            station=station,
            date=reservation_date
        ))

    now = datetime.now()
    booking_start = datetime.combine(
        selected_date - timedelta(days=1),
        time(18, 30)
    )
    departure_datetime = get_departure_datetime(
        reservation_date,
        departure_time
    )

    # 과거 시간대와 예약 시작 전 시간대를 서버에서 차단한다.
    if now < booking_start or now > departure_datetime:
        flash(
            "예약 가능한 시간이 아닙니다. "
            "예약은 탑승 전날 18:30부터 가능합니다."
        )
        return redirect(url_for(
            "reserve",
            direction=direction,
            station=station,
            date=reservation_date
        ))

    conn = get_db()

    try:
        # 예약 확인 + 좌석 배정 + INSERT를 하나의 쓰기 트랜잭션으로 처리한다.
        # 동시에 여러 요청이 들어와도 같은 좌석을 중복 배정하지 않도록 한다.
        conn.execute("BEGIN IMMEDIATE")

        # 같은 학생이 같은 날짜·방향으로 이미 예약했는지 확인한다.
        # 정류장과 시간은 중복 기준에 포함하지 않으므로, 등교 예약을 한 경우
        # 다른 정류장이나 다른 시간의 등교 예약도 추가로 할 수 없다.
        # 반대로 등교와 하교는 방향이 다르므로 각각 1건씩 예약할 수 있다.
        duplicate = conn.execute("""
            SELECT id, station, departure_time
            FROM reservations
            WHERE student_id = ?
              AND reservation_date = ?
              AND direction = ?
        """, (
            student_id,
            reservation_date,
            direction
        )).fetchone()

        if duplicate:
            conn.rollback()

            # 완전히 같은 예약이면 일반 중복 메시지를 표시하고,
            # 같은 방향의 다른 정류장/시간이면 방향별 중복 메시지를 표시한다.
            if (
                duplicate["station"] == station
                and duplicate["departure_time"] == departure_time
            ):
                duplicate_message = "예약 내역이 이미 있습니다."
            else:
                duplicate_message = (
                    "등교 예약 내역이 이미 있습니다."
                    if direction == "등교"
                    else "하교 예약 내역이 이미 있습니다."
                )

            flash(duplicate_message, "duplicate")
            return redirect(url_for(
                "reserve",
                direction=direction,
                station=station,
                date=reservation_date
            ))

        # 남아 있는 버스와 좌석 중 가장 먼저 사용 가능한 좌석을 자동 배정한다.
        bus_number, seat_number = find_available_bus_and_seat(
            conn,
            reservation_date,
            station,
            direction,
            departure_time,
            target["buses"]
        )

        if bus_number is None:
            conn.rollback()
            flash("해당 배차는 정원이 마감되었습니다.")
            return redirect(url_for(
                "reserve",
                direction=direction,
                station=station,
                date=reservation_date
            ))

        # 검증이 모두 통과되면 최종 예약 정보를 SQLite에 저장한다.
        conn.execute("""
            INSERT INTO reservations (
                student_id,
                reservation_date,
                station,
                direction,
                departure_time,
                bus_number,
                seat_number,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            student_id,
            reservation_date,
            station,
            direction,
            departure_time,
            bus_number,
            seat_number,
            datetime.now().isoformat(timespec="seconds")
        ))

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()
        duplicate_message = (
            "등교 예약 내역이 이미 있습니다."
            if direction == "등교"
            else "하교 예약 내역이 이미 있습니다."
        )
        flash(duplicate_message, "duplicate")
        return redirect(url_for(
            "reserve",
            direction=direction,
            station=station,
            date=reservation_date
        ))

    finally:
        conn.close()

    return render_template(
        "reservation_complete.html",
        station=station,
        direction=direction,
        reservation_date=selected_date.strftime("%Y년 %m월 %d일"),
        day_name=DAY_NAMES[selected_date.weekday()],
        departure_time=departure_time,
        bus_number=bus_number,
        seat_number=seat_number
    )


@app.route("/my-reservations")
# 로그인한 학생의 예약 목록과 각 예약의 취소 가능 여부를 조회한다.
def my_reservations():
    if "student_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            reservation_date,
            station,
            direction,
            departure_time,
            bus_number,
            seat_number
        FROM reservations
        WHERE student_id = ?
        ORDER BY reservation_date, departure_time
    """, (session["student_id"],)).fetchall()

    conn.close()

    reservations = []

    for row in rows:
        departure_datetime = get_departure_datetime(
            row["reservation_date"],
            row["departure_time"]
        )

        cancel_deadline = departure_datetime - timedelta(
            minutes=CANCELLATION_LIMIT_MINUTES
        )

        reservations.append({
            "id": row["id"],
            "reservation_date": row["reservation_date"],
            "reservation_date_display": datetime.strptime(
                row["reservation_date"],
                "%Y-%m-%d"
            ).strftime("%Y년 %m월 %d일"),
            "day_name": DAY_NAMES[
                datetime.strptime(
                    row["reservation_date"],
                    "%Y-%m-%d"
                ).weekday()
            ],
            "station": row["station"],
            "direction": row["direction"],
            "departure_time": row["departure_time"],
            "bus_number": row["bus_number"],
            "seat_number": row["seat_number"],
            "cancel_allowed": datetime.now() <= cancel_deadline,
            "cancel_deadline": cancel_deadline.strftime("%H:%M"),
        })

    return render_template(
        "my_reservations.html",
        reservations=reservations
    )


@app.route("/cancel/<int:reservation_id>", methods=["POST"])
# 본인의 예약을 확인한 뒤 출발 10분 전까지 취소하고 좌석을 다시 사용 가능하게 만든다.
def cancel_reservation(reservation_id):
    if "student_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        reservation = conn.execute("""
            SELECT *
            FROM reservations
            WHERE id = ?
              AND student_id = ?
        """, (
            reservation_id,
            session["student_id"]
        )).fetchone()

        if reservation is None:
            conn.rollback()
            flash("취소할 예약을 찾을 수 없습니다.")
            return redirect(url_for("my_reservations"))

        departure_datetime = get_departure_datetime(
            reservation["reservation_date"],
            reservation["departure_time"]
        )
        cancel_deadline = departure_datetime - timedelta(
            minutes=CANCELLATION_LIMIT_MINUTES
        )

        if datetime.now() > cancel_deadline:
            conn.rollback()
            flash("출발 10분 전부터는 예약을 취소할 수 없습니다.")
            return redirect(url_for("my_reservations"))

        # 예약 데이터를 삭제하면 해당 버스의 좌석이 다시 빈 좌석으로 계산된다.
        conn.execute(
            "DELETE FROM reservations WHERE id = ? AND student_id = ?",
            (reservation_id, session["student_id"])
        )

        conn.commit()

    finally:
        conn.close()

    flash("예약이 취소되었습니다. 좌석이 다시 예약 가능 상태로 변경되었습니다.", "cancel_success")
    return redirect(url_for("my_reservations"))



if __name__ == "__main__":
    init_db()
    app.run(debug=True)

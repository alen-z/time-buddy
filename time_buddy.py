#!/usr/bin/env python3
import argparse
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

__version__ = "1.2.0"

def get_version():
    """Returns the package version."""
    try:
        from importlib.metadata import version
        return version("time-buddy")
    except Exception:
        return __version__

# --- Configuration ---
DEFAULT_EXPECTED_HOURS_PER_DAY = 7.5

def get_db_path():
    """Returns the platform-specific path to the database file."""
    app_name = "TimeBuddy"
    # For macOS, use the Application Support directory
    home = os.path.expanduser("~")
    app_support_dir = os.path.join(home, "Library", "Application Support", app_name)

    if not os.path.exists(app_support_dir):
        os.makedirs(app_support_dir)

    return os.path.join(app_support_dir, 'time_buddy.db')

DB_FILE = get_db_path()


# --- Database Functions ---
def db_connect():
    """Connects to the SQLite database."""
    return sqlite3.connect(DB_FILE)

def db_init(conn):
    """Initializes the database schema."""
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_logs (
                day TEXT,
                timestamp TEXT,
                data TEXT,
                UNIQUE(day, timestamp, data)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fetched_days (
                day TEXT PRIMARY KEY
            )
        """)

def db_is_day_cached(conn, day):
    """Checks if a past day has been fully cached."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM fetched_days WHERE day = ?", (day.isoformat(),))
    return cursor.fetchone() is not None

def db_get_logs_for_day(conn, day):
    """Retrieves all log entries for a given day from the cache."""
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM raw_logs WHERE day = ?", (day.isoformat(),))
    return [json.loads(row[0]) for row in cursor.fetchall()]

def db_cache_logs(conn, day, logs):
    """Caches a list of log entries for a given day."""
    log_data = [(day.isoformat(), entry.get("timestamp"), json.dumps(entry)) for entry in logs]
    with conn:
        conn.executemany("INSERT OR IGNORE INTO raw_logs (day, timestamp, data) VALUES (?, ?, ?)", log_data)

def db_mark_day_as_cached(conn, day):
    """Marks a day as fully fetched in the database."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO fetched_days (day) VALUES (?)", (day.isoformat(),))


# --- Export Functions ---
def build_day_record(day: date, hourly_durations: defaultdict, block_duration: timedelta, expected_hours: float):
    """Builds a dictionary record for a single day's data."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    total_duration = sum(hourly_durations.values(), timedelta())
    total_hours = total_duration.total_seconds() / 3600
    total_block_hours = block_duration.total_seconds() / 3600
    raw_percentage = (total_hours / expected_hours) * 100
    block_percentage = (total_block_hours / expected_hours) * 100

    # Build hourly breakdown (minutes per hour)
    hourly_minutes = {}
    for hour in range(24):
        minutes = hourly_durations.get(hour, timedelta()).total_seconds() / 60
        hourly_minutes[hour] = round(minutes, 1)

    return {
        "date": day.isoformat(),
        "day_of_week": day_names[day.weekday()],
        "is_weekend": day.weekday() >= 5,
        "raw_hours": round(total_hours, 2),
        "raw_percentage": round(raw_percentage, 1),
        "block_hours": round(total_block_hours, 2),
        "block_percentage": round(block_percentage, 1),
        "hourly_minutes": hourly_minutes,
    }


def export_json(records: list, summary: dict):
    """Exports data as JSON to stdout."""
    output = {
        "days": records,
        "summary": summary,
    }
    print(json.dumps(output, indent=2))


def export_csv(records: list, summary: dict):
    """Exports data as CSV to stdout."""
    if not records:
        return

    output = io.StringIO()
    fieldnames = [
        "date", "day_of_week", "is_weekend",
        "raw_hours", "raw_percentage", "block_hours", "block_percentage"
    ]
    # Add hourly columns
    for hour in range(24):
        fieldnames.append(f"hour_{hour:02d}")

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for record in records:
        row = {
            "date": record["date"],
            "day_of_week": record["day_of_week"],
            "is_weekend": record["is_weekend"],
            "raw_hours": record["raw_hours"],
            "raw_percentage": record["raw_percentage"],
            "block_hours": record["block_hours"],
            "block_percentage": record["block_percentage"],
        }
        # Add hourly data
        for hour in range(24):
            row[f"hour_{hour:02d}"] = record["hourly_minutes"].get(hour, 0)
        writer.writerow(row)

    # Add empty row and summary
    writer.writerow({})
    summary_row = {
        "date": "TOTAL",
        "day_of_week": f"{summary['total_days']} days",
        "is_weekend": "",
        "raw_hours": summary["total_raw_hours"],
        "raw_percentage": summary["total_raw_percentage"],
        "block_hours": summary["total_block_hours"],
        "block_percentage": summary["total_block_percentage"],
    }
    writer.writerow(summary_row)

    print(output.getvalue(), end="")


def export_pdf(records: list, summary: dict):
    """Exports data as PDF to stdout."""
    from fpdf import FPDF

    class TimeBuddyPDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 16)
            self.cell(0, 10, 'Time Buddy Report', align='C', new_x='LMARGIN', new_y='NEXT')
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font('Helvetica', 'I', 8)
            self.set_text_color(128)
            self.cell(0, 10, f'Page {self.page_no()}', align='C')

    pdf = TimeBuddyPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Report metadata
    pdf.set_font('Helvetica', '', 10)
    pdf.set_text_color(100)
    if records:
        date_range = f"{records[0]['date']} to {records[-1]['date']}"
        pdf.cell(0, 6, f"Period: {date_range}", new_x='LMARGIN', new_y='NEXT')
    pdf.cell(0, 6, f"Expected hours/day: {summary.get('expected_hours_per_day', 7.5)}", new_x='LMARGIN', new_y='NEXT')
    pdf.ln(8)

    # Table header
    pdf.set_fill_color(45, 55, 72)
    pdf.set_text_color(255)
    pdf.set_font('Helvetica', 'B', 9)

    col_widths = [25, 15, 22, 18, 22, 18, 70]
    headers = ['Date', 'Day', 'Raw (h)', 'Raw %', 'Block (h)', 'Block %', 'Activity (24h)']

    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 8, header, border=1, align='C', fill=True)
    pdf.ln()

    # Table rows
    pdf.set_font('Helvetica', '', 8)
    pdf.set_text_color(0)

    for idx, record in enumerate(records):
        # Alternate row colors
        if idx % 2 == 0:
            pdf.set_fill_color(249, 250, 251)
        else:
            pdf.set_fill_color(255, 255, 255)

        # Weekend rows get a slight blue tint
        if record['is_weekend']:
            pdf.set_fill_color(239, 246, 255)

        pdf.cell(col_widths[0], 7, record['date'], border=1, align='C', fill=True)
        pdf.cell(col_widths[1], 7, record['day_of_week'], border=1, align='C', fill=True)
        pdf.cell(col_widths[2], 7, f"{record['raw_hours']:.1f}", border=1, align='C', fill=True)
        pdf.cell(col_widths[3], 7, f"{record['raw_percentage']:.0f}%", border=1, align='C', fill=True)
        pdf.cell(col_widths[4], 7, f"{record['block_hours']:.1f}", border=1, align='C', fill=True)
        pdf.cell(col_widths[5], 7, f"{record['block_percentage']:.0f}%", border=1, align='C', fill=True)

        # Activity visualization (24 blocks)
        x_start = pdf.get_x()
        y_pos = pdf.get_y()
        block_width = col_widths[6] / 24

        for hour in range(24):
            minutes = record['hourly_minutes'].get(hour, 0)
            # Color based on activity (gray to green gradient)
            if minutes > 0:
                intensity = min(int((minutes / 60) * 255), 255)
                pdf.set_fill_color(100, 150 + int(intensity * 0.4), 100)
            else:
                pdf.set_fill_color(230, 230, 230)

            pdf.rect(x_start + (hour * block_width), y_pos, block_width, 7, style='F')

        # Draw border around activity cell
        pdf.rect(x_start, y_pos, col_widths[6], 7, style='D')
        pdf.set_xy(x_start + col_widths[6], y_pos)
        pdf.ln()

    # Summary row
    pdf.ln(3)
    pdf.set_fill_color(45, 55, 72)
    pdf.set_text_color(255)
    pdf.set_font('Helvetica', 'B', 9)

    pdf.cell(col_widths[0], 8, 'TOTAL', border=1, align='C', fill=True)
    pdf.cell(col_widths[1], 8, f"{summary['total_days']}d", border=1, align='C', fill=True)
    pdf.cell(col_widths[2], 8, f"{summary['total_raw_hours']:.1f}", border=1, align='C', fill=True)
    pdf.cell(col_widths[3], 8, f"{summary['total_raw_percentage']:.0f}%", border=1, align='C', fill=True)
    pdf.cell(col_widths[4], 8, f"{summary['total_block_hours']:.1f}", border=1, align='C', fill=True)
    pdf.cell(col_widths[5], 8, f"{summary['total_block_percentage']:.0f}%", border=1, align='C', fill=True)
    pdf.cell(col_widths[6], 8, '', border=1, fill=True)
    pdf.ln()

    # Legend
    pdf.ln(8)
    pdf.set_text_color(100)
    pdf.set_font('Helvetica', 'I', 8)
    pdf.cell(0, 5, 'Activity visualization: Each block represents 1 hour (00:00-23:00). Darker green = more activity.', new_x='LMARGIN', new_y='NEXT')

    # Output PDF to stdout as binary
    pdf_bytes = pdf.output()
    sys.stdout.buffer.write(pdf_bytes)


def print_hourly_breakdown(day: date, hourly_durations: defaultdict, block_duration: timedelta, expected_hours: float):
    """Prints a single line of 24 colored blocks representing a day's screen time."""
    # --- Color gradient (10 steps from red to green in ANSI 256-color) ---
    gradient_colors = [196, 202, 208, 214, 220, 226, 190, 154, 118, 46]

    # Use build_day_record for all calculations
    record = build_day_record(day, hourly_durations, block_duration, expected_hours)

    # Use cyan for weekends, default color for weekdays
    if record["is_weekend"]:
        day_label = f" \033[38;5;51m({record['day_of_week']})\033[0m"
    else:
        day_label = f" ({record['day_of_week']})"

    output_line = f"{record['date']}{day_label}: "

    for hour in range(24):
        minutes = record["hourly_minutes"][hour]

        color_code = ""
        if minutes > 0:
            # Map minutes (1-60) to a gradient index (0-9)
            gradient_index = min(int((minutes - 1) / 6), len(gradient_colors) - 1)
            ansi_color = gradient_colors[gradient_index]
            color_code = f'\033[38;5;{ansi_color}m'
        else:
            # Use a faint grey for hours with no activity
            color_code = '\033[38;5;240m'

        output_line += f"{color_code}█\033[0m"

    raw_str = f"Raw: {record['raw_hours']:.1f} h ({record['raw_percentage']:.0f}%)"
    block_str = f"Block: {record['block_hours']:.1f} h ({record['block_percentage']:.0f}%)"
    output_line += f"  {raw_str:<22}{block_str}"
    print(output_line)


def process_day_logs(logs, current_day, verbose=False):
    """Processes log entries for a single day and returns hourly durations and block duration."""
    events = []
    for entry in logs:
        timestamp_str = entry.get("timestamp")
        if not timestamp_str:
            continue

        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                continue

        # Ensure the event belongs to the current day being processed
        if timestamp.date() != current_day:
            continue

        message = entry.get("eventMessage", "")

        if "screenIsUnlocked" in message:
            events.append({'timestamp': timestamp, 'type': 'unlocked'})
        elif "screenIsLocked" in message:
            events.append({'timestamp': timestamp, 'type': 'locked'})

    events.sort(key=lambda x: x['timestamp'])

    # --- Calculate precise screen time (sum of unlock-to-lock sessions) ---
    hourly_durations = defaultdict(timedelta)
    unlock_time = None
    if verbose:
        print(f"Processing sessions for {current_day.isoformat()}:")

    for event in events:
        if event['type'] == 'unlocked':
            if unlock_time is None:
                unlock_time = event['timestamp']
        elif event['type'] == 'locked':
            if unlock_time is not None:
                lock_time = event['timestamp']
                duration = lock_time - unlock_time
                if verbose:
                    print(f"  - Session from {unlock_time.strftime('%Y-%m-%d %H:%M:%S')} to {lock_time.strftime('%Y-%m-%d %H:%M:%S')} (Duration: {duration})")

                current_time = unlock_time
                while current_time < lock_time:
                    current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
                    next_hour_start = current_hour_start + timedelta(hours=1)

                    segment_end = min(lock_time, next_hour_start)
                    duration_in_hour = segment_end - current_time

                    hourly_durations[current_time.hour] += duration_in_hour

                    current_time = next_hour_start

                unlock_time = None

    # --- Calculate Block Time (total span of continuous activity) ---
    total_block_duration = timedelta()
    active_hours = sorted([h for h, d in hourly_durations.items() if d.total_seconds() > 0])

    if active_hours:
        current_block_start_hour = active_hours[0]
        for i in range(1, len(active_hours)):
            # If there's a gap of more than an hour, the block is broken
            if active_hours[i] > active_hours[i-1] + 1:
                # Process the completed block
                block_end_hour = active_hours[i-1]

                first_event_in_block = min([e['timestamp'] for e in events if e['timestamp'].hour == current_block_start_hour])
                last_event_in_block = max([e['timestamp'] for e in events if e['timestamp'].hour == block_end_hour])

                total_block_duration += last_event_in_block - first_event_in_block

                # Start a new block
                current_block_start_hour = active_hours[i]

        # Process the final block
        last_block_end_hour = active_hours[-1]
        first_event_in_block = min([e['timestamp'] for e in events if e['timestamp'].hour == current_block_start_hour])
        last_event_in_block = max([e['timestamp'] for e in events if e['timestamp'].hour == last_block_end_hour])
        total_block_duration += last_event_in_block - first_event_in_block

    return hourly_durations, total_block_duration, unlock_time


def fetch_period_data(start_date, end_date, verbose=False, no_cache=False, spinner=None, conn=None, local_tz=None):
    """
    Fetches and processes screen time data for a specific date range.

    Args:
        start_date: First day of the period (inclusive)
        end_date: Last day of the period (inclusive)
        verbose: Print detailed session information
        no_cache: Force refetching of all logs
        spinner: Optional Halo spinner for progress display
        conn: Database connection
        local_tz: Local timezone

    Returns:
        Tuple of (daily_hourly_durations, daily_block_durations, days_with_activity)
    """
    daily_hourly_durations = {}
    daily_block_durations = {}
    days_with_activity = set()
    today = datetime.now().date()

    # Generate dates chronologically
    num_days = (end_date - start_date).days + 1
    dates_to_process = [start_date + timedelta(days=i) for i in range(num_days)]

    # Track whether the previous processed day ended unlocked (carry-over)
    carry_over_unlocked = False
    carry_over_tzinfo = None

    for current_day in dates_to_process:
        if spinner:
            spinner.text = f"Processing {current_day.isoformat()}..."

        logs = []
        is_cached = not no_cache and db_is_day_cached(conn, current_day)

        # Past days can be loaded from cache. Today is always fetched fresh.
        if is_cached and current_day != today:
            if spinner:
                spinner.text = f"Loading logs from cache for {current_day.isoformat()}..."
            logs = db_get_logs_for_day(conn, current_day)
            if verbose:
                print(f"\nLoaded {len(logs)} log entries from cache for {current_day.isoformat()}.")
        else:
            if spinner:
                spinner.text = f"Fetching logs for {current_day.isoformat()}..."

            start_of_day = datetime.combine(current_day, datetime.min.time())
            end_of_day = datetime.combine(current_day, datetime.max.time())
            start_of_day_aware = start_of_day.replace(tzinfo=local_tz)
            end_of_day_aware = end_of_day.replace(tzinfo=local_tz)

            if verbose:
                print(f"\nFetching logs for {current_day.isoformat()}...")

            predicate = 'process == "loginwindow" and eventMessage contains "com.apple.sessionagent.screenIs"'
            command = [
                'log', 'show', '--style', 'json',
                '--predicate', predicate,
                '--start', start_of_day_aware.strftime('%Y-%m-%d %H:%M:%S%z'),
                '--end', end_of_day_aware.strftime('%Y-%m-%d %H:%M:%S%z')
            ]

            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                fetched_logs = json.loads(result.stdout)
                logs.extend(fetched_logs)

                if verbose:
                    print(f"Found {len(logs)} log entries.")

                # Cache the newly fetched logs
                db_cache_logs(conn, current_day, logs)
                if current_day != today:
                    db_mark_day_as_cached(conn, current_day)

            except subprocess.CalledProcessError as e:
                if e.returncode == 1 and not e.stdout and not e.stderr:
                    pass  # No logs found
                else:
                    if spinner:
                        spinner.fail(f"Error executing log command for {current_day.isoformat()}")
                    print(f"Error executing log command for {current_day.isoformat()}: {e}")
            except json.JSONDecodeError:
                if spinner:
                    spinner.fail(f"Error decoding JSON from log output for {current_day.isoformat()}")
                print(f"Error decoding JSON from log output for {current_day.isoformat()}.")

        if not logs:
            carry_over_unlocked = False
            continue

        # Inject a synthetic unlocked event at 00:00 only if previous day carried over unlocked
        if carry_over_unlocked:
            start_of_day = datetime.combine(current_day, datetime.min.time())
            tzinfo = carry_over_tzinfo or local_tz
            start_of_day = start_of_day.replace(tzinfo=tzinfo)
            logs.append({
                "timestamp": start_of_day.isoformat(),
                "eventMessage": "screenIsUnlocked (synthetic carryover)"
            })

        hourly_durations, block_duration, last_unlock_time = process_day_logs(logs, current_day, verbose)

        # If the last event of the day was an unlock, it's an open session
        if last_unlock_time is not None:
            # If it's today, calculate up to now
            if current_day == today:
                now = datetime.now(local_tz)
                if last_unlock_time.date() == today:
                    duration = now - last_unlock_time
                    if verbose:
                        print(f"  - Active session: from {last_unlock_time.strftime('%H:%M:%S')} to now (Duration: {duration})")

                    current_time = last_unlock_time
                    while current_time < now:
                        current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
                        next_hour_start = current_hour_start + timedelta(hours=1)

                        segment_end = min(now, next_hour_start)
                        duration_in_hour = segment_end - current_time

                        hourly_durations[current_time.hour] += duration_in_hour

                        current_time = next_hour_start

                    block_duration += now - last_unlock_time
            # If it's a past day, calculate up to midnight
            else:
                end_of_day = datetime.combine(current_day, time(23, 59, 59, 999999), tzinfo=last_unlock_time.tzinfo)
                duration = end_of_day - last_unlock_time
                if verbose:
                    print(f"  - Session carried over to next day: from {last_unlock_time.strftime('%H:%M:%S')} to 23:59:59 (Duration: {duration})")

                current_time = last_unlock_time
                while current_time < end_of_day:
                    current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
                    next_hour_start = current_hour_start + timedelta(hours=1)

                    segment_end = min(end_of_day, next_hour_start)
                    duration_in_hour = segment_end - current_time

                    hourly_durations[current_time.hour] += duration_in_hour

                    current_time = next_hour_start

                block_duration += end_of_day - last_unlock_time

        # Update carry-over state for the next day
        if last_unlock_time is not None:
            carry_over_unlocked = True
            carry_over_tzinfo = last_unlock_time.tzinfo
        else:
            carry_over_unlocked = False
            carry_over_tzinfo = None

        if any(duration.total_seconds() > 0 for duration in hourly_durations.values()):
            daily_hourly_durations[current_day] = hourly_durations
            daily_block_durations[current_day] = block_duration
            days_with_activity.add(current_day)
            if verbose:
                total_day_hours = sum(hourly_durations.values(), timedelta()).total_seconds() / 3600
                print(f"Calculated {total_day_hours:.1f} hours of screen time.")

    return daily_hourly_durations, daily_block_durations, days_with_activity


def calculate_period_summary(daily_hourly_durations, daily_block_durations, days_with_activity, include_weekends, expected_hours):
    """
    Calculates summary statistics for a period.

    Returns:
        Dictionary with total_days, total_raw_hours, total_raw_percentage, total_block_hours, total_block_percentage
    """
    if not daily_hourly_durations:
        return {
            "total_days": 0,
            "total_raw_hours": 0,
            "total_raw_percentage": 0,
            "total_block_hours": 0,
            "total_block_percentage": 0,
            "expected_hours_per_day": expected_hours,
            "include_weekends": include_weekends,
        }

    total_actual_hours = 0
    for day_data in daily_hourly_durations.values():
        total_actual_hours += sum(day_data.values(), timedelta()).total_seconds() / 3600

    total_block_hours = sum([d.total_seconds() for d in daily_block_durations.values()]) / 3600

    # Calculate expected hours based on include_weekends flag
    if include_weekends:
        total_expected_hours = len(days_with_activity) * expected_hours
    else:
        weekday_count = sum(1 for day in days_with_activity if day.weekday() < 5)
        total_expected_hours = weekday_count * expected_hours

    total_days = len(days_with_activity)
    if total_expected_hours > 0:
        monthly_raw_percentage = (total_actual_hours / total_expected_hours) * 100
        monthly_block_percentage = (total_block_hours / total_expected_hours) * 100
    else:
        monthly_raw_percentage = 0
        monthly_block_percentage = 0

    return {
        "total_days": total_days,
        "total_raw_hours": round(total_actual_hours, 2),
        "total_raw_percentage": round(monthly_raw_percentage, 1),
        "total_block_hours": round(total_block_hours, 2),
        "total_block_percentage": round(monthly_block_percentage, 1),
        "expected_hours_per_day": expected_hours,
        "include_weekends": include_weekends,
    }


def format_trend(current_value, previous_value):
    """
    Formats a trend indicator comparing current to previous value.

    Returns:
        String like "↑ +15.2%" or "↓ -8.3%" or "→ 0.0%"
    """
    if previous_value == 0:
        if current_value > 0:
            return "\033[38;5;46m↑ +∞\033[0m"
        return "\033[38;5;240m→ 0.0%\033[0m"

    diff = current_value - previous_value
    pct_change = (diff / previous_value) * 100

    if pct_change > 0:
        return f"\033[38;5;46m↑ +{pct_change:.1f}%\033[0m"
    elif pct_change < 0:
        return f"\033[38;5;196m↓ {pct_change:.1f}%\033[0m"
    else:
        return f"\033[38;5;240m→ 0.0%\033[0m"


def get_screen_time(days_back, verbose=False, no_cache=False, include_weekends=False, expected_hours=DEFAULT_EXPECTED_HOURS_PER_DAY, export_format=None, offset=0, compare_offset=None):
    """
    Calculates screen time for the last N days, fetching logs day by day.

    Args:
        days_back: Number of days to look back
        verbose: Print detailed session information
        no_cache: Force refetching of all logs
        include_weekends: Count weekends toward expected work hours (default: False)
        expected_hours: Expected working hours per day (default: 7.5)
        export_format: Export format ('csv', 'json', or None for visual output)
        offset: Shift the period back by this many days from today (default: 0)
        compare_offset: If provided, compare with period offset by this many days
    """
    # Lazy imports for faster --version response (third-party libs are slow to import)
    from tzlocal import get_localzone
    from halo import Halo
    import colorama

    conn = db_connect()
    db_init(conn)

    today = datetime.now().date()
    local_tz = get_localzone()

    # Define current period (with optional offset from today)
    current_end_date = today - timedelta(days=offset)
    current_start_date = current_end_date - timedelta(days=days_back - 1)

    # Define comparison period if requested
    compare_start_date = None
    compare_end_date = None
    if compare_offset is not None:
        compare_end_date = current_start_date - timedelta(days=compare_offset + 1)
        compare_start_date = compare_end_date - timedelta(days=days_back - 1)

    # Disable spinner and visual output for export mode
    is_export_mode = export_format is not None
    spinner = None
    if not verbose and not is_export_mode:
        spinner = Halo(text='Initializing...', spinner='dots')
        spinner.start()

    daily_hourly_durations = {}
    daily_block_durations = {}
    days_with_activity = set()

    compare_hourly_durations = {}
    compare_block_durations = {}
    compare_days_with_activity = set()

    try:
        # Fetch comparison period first (if requested)
        if compare_offset is not None:
            if spinner:
                spinner.text = f"Fetching comparison period ({compare_start_date} to {compare_end_date})..."
            compare_hourly_durations, compare_block_durations, compare_days_with_activity = fetch_period_data(
                compare_start_date, compare_end_date, verbose, no_cache, spinner, conn, local_tz
            )

        # Fetch current period
        if spinner:
            spinner.text = f"Fetching current period ({current_start_date} to {current_end_date})..."
        daily_hourly_durations, daily_block_durations, days_with_activity = fetch_period_data(
            current_start_date, current_end_date, verbose, no_cache, spinner, conn, local_tz
        )

    except KeyboardInterrupt:
        if spinner:
            spinner.warn("Process interrupted by user.")
        print("\n\nProcess interrupted by user. Displaying summary for data collected so far...")

    finally:
        conn.close()

    if spinner:
        spinner.succeed("Log processing complete.")
        spinner.stop()
        colorama.reinit()

    # Calculate summaries
    summary = calculate_period_summary(daily_hourly_durations, daily_block_durations, days_with_activity, include_weekends, expected_hours)
    compare_summary = None
    if compare_offset is not None:
        compare_summary = calculate_period_summary(compare_hourly_durations, compare_block_durations, compare_days_with_activity, include_weekends, expected_hours)

    # --- Handle empty data ---
    if not daily_hourly_durations:
        if is_export_mode:
            empty_summary = {"total_days": 0, "total_raw_hours": 0, "total_raw_percentage": 0, "total_block_hours": 0, "total_block_percentage": 0}
            if export_format == 'json':
                export_json([], empty_summary)
            elif export_format == 'csv':
                export_csv([], empty_summary)
            elif export_format == 'pdf':
                export_pdf([], empty_summary)
        else:
            print("\n--- Daily Screen Time Summary ---")
            print("No screen time data found for the selected period.")
        return

    # --- Export Mode ---
    if is_export_mode:
        sorted_days = sorted(daily_hourly_durations.keys())
        records = [
            build_day_record(day, daily_hourly_durations[day], daily_block_durations[day], expected_hours)
            for day in sorted_days
        ]

        if export_format == 'json':
            export_json(records, summary)
        elif export_format == 'csv':
            export_csv(records, summary)
        elif export_format == 'pdf':
            export_pdf(records, summary)
        return

    # --- Visual Output Mode ---
    if compare_summary is not None:
        # Show comparison period first
        print(f"\n--- Previous Period ({compare_start_date} to {compare_end_date}) ---")
        if compare_hourly_durations:
            sorted_compare_days = sorted(compare_hourly_durations.keys())
            for day in sorted_compare_days:
                print_hourly_breakdown(day, compare_hourly_durations[day], compare_block_durations[day], expected_hours)
        else:
            print("No screen time data found for this period.")

        # Then show current period
        print(f"\n--- Current Period ({current_start_date} to {current_end_date}) ---")
        sorted_days = sorted(daily_hourly_durations.keys())
        for day in sorted_days:
            print_hourly_breakdown(day, daily_hourly_durations[day], daily_block_durations[day], expected_hours)
    else:
        print("\n--- Daily Screen Time Summary ---")
        sorted_days = sorted(daily_hourly_durations.keys())
        for day in sorted_days:
            print_hourly_breakdown(day, daily_hourly_durations[day], daily_block_durations[day], expected_hours)

    # --- Total Summary ---
    print("\n--- Total Summary ---")
    total_expected_hours = summary["total_days"] * expected_hours if include_weekends else sum(1 for day in days_with_activity if day.weekday() < 5) * expected_hours

    if total_expected_hours > 0:
        raw_str = f"Raw: {summary['total_raw_hours']:.1f} h ({summary['total_raw_percentage']:.0f}%)"
        block_str = f"Block: {summary['total_block_hours']:.1f} h ({summary['total_block_percentage']:.0f}%)"

        if not include_weekends:
            weekday_count = sum(1 for day in days_with_activity if day.weekday() < 5)
            weekend_count = summary['total_days'] - weekday_count
            day_breakdown = f"{summary['total_days']} active day(s)"
            if weekend_count > 0:
                day_breakdown += f" ({weekday_count} weekdays, {weekend_count} weekend days)"
        else:
            day_breakdown = f"{summary['total_days']} active day(s)"

        print(f"Total for {day_breakdown}: {raw_str:<22}{block_str}")
    else:
        print("No activity to summarize.")

    # --- Comparison Output ---
    if compare_summary is not None:
        print("\n--- Comparison (Current vs Previous) ---")
        print(f"            {'Current':>9}      {'Previous':>9}")

        # Raw time comparison
        raw_trend = format_trend(summary['total_raw_hours'], compare_summary['total_raw_hours'])
        print(f"Raw Time:   {summary['total_raw_hours']:>6.1f} h  vs  {compare_summary['total_raw_hours']:>6.1f} h  {raw_trend}")

        # Block time comparison
        block_trend = format_trend(summary['total_block_hours'], compare_summary['total_block_hours'])
        print(f"Block Time: {summary['total_block_hours']:>6.1f} h  vs  {compare_summary['total_block_hours']:>6.1f} h  {block_trend}")


def main():
    """
    Main function to run the CLI.
    """
    parser = argparse.ArgumentParser(description="A simple to use time tracking CLI for macOS.")
    parser.add_argument(
        '-V', '--version',
        action='version',
        version=f'time-buddy {get_version()}'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Number of days back to calculate screen time for. (default: 7)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Print detailed session information for validation.'
    )
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help='Force refetching of all logs, ignoring the cache.'
    )
    parser.add_argument(
        '--clear-cache',
        action='store_true',
        help='Delete the cache file and exit.'
    )
    parser.add_argument(
        '--include-weekends',
        action='store_true',
        help='Include weekends in expected work hours calculation. (default: False)'
    )
    parser.add_argument(
        '--expected-hours',
        type=float,
        default=DEFAULT_EXPECTED_HOURS_PER_DAY,
        help=f'Expected working hours per day. (default: {DEFAULT_EXPECTED_HOURS_PER_DAY})'
    )
    parser.add_argument(
        '--export',
        type=str,
        choices=['csv', 'json', 'pdf'],
        default=None,
        metavar='FORMAT',
        help='Export data in specified format (csv or json) instead of visual output.'
    )
    parser.add_argument(
        '--offset',
        type=int,
        default=0,
        metavar='DAYS',
        help='Shift the period back by DAYS from today. E.g., --days 7 --offset 30 shows 7 days ending 30 days ago. (default: 0)'
    )
    parser.add_argument(
        '--compare-offset',
        type=int,
        nargs='?',
        const=0,
        default=None,
        metavar='DAYS',
        help='Compare current period with a previous period. DAYS is the gap between periods (default: 0 for consecutive periods). Use 7 to skip a week.'
    )
    args = parser.parse_args()

    if args.clear_cache:
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
            print(f"Cache file '{DB_FILE}' has been deleted.")
        else:
            print("No cache file to delete.")
        return

    get_screen_time(args.days, args.verbose, args.no_cache, args.include_weekends, args.expected_hours, args.export, args.offset, args.compare_offset)


if __name__ == "__main__":
    main()

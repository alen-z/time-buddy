#!/usr/bin/env python3
import json
import subprocess
from datetime import datetime, timedelta, date, time
import argparse
from collections import defaultdict
from tzlocal import get_localzone
from halo import Halo
import colorama
import sqlite3
import os

# --- Configuration ---
EXPECTED_HOURS_PER_DAY = 7.5

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


def print_hourly_breakdown(day: date, hourly_durations: defaultdict, block_duration: timedelta):
    """Prints a single line of 24 colored blocks representing a day's screen time."""
    # --- Color gradient (10 steps from red to green in ANSI 256-color) ---
    gradient_colors = [196, 202, 208, 214, 220, 226, 190, 154, 118, 46]
    
    total_duration = sum(hourly_durations.values(), timedelta())
    total_hours = total_duration.total_seconds() / 3600
    total_block_hours = block_duration.total_seconds() / 3600
    raw_percentage = (total_hours / EXPECTED_HOURS_PER_DAY) * 100
    block_percentage = (total_block_hours / EXPECTED_HOURS_PER_DAY) * 100

    output_line = f"{day.isoformat()}: "

    for hour in range(24):
        minutes = hourly_durations.get(hour, timedelta()).total_seconds() / 60
        
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
    
    raw_str = f"Raw: {total_hours:.1f} h ({raw_percentage:.0f}%)"
    block_str = f"Block: {total_block_hours:.1f} h ({block_percentage:.0f}%)"
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


def get_screen_time(days_back, verbose=False, no_cache=False):
    """
    Calculates screen time for the last N days, fetching logs day by day.
    """
    conn = db_connect()
    db_init(conn)

    daily_hourly_durations = {}
    daily_block_durations = {}
    today = datetime.now().date()
    total_actual_hours = 0
    days_with_activity = set()
    local_tz = get_localzone()

    spinner = None
    if not verbose:
        spinner = Halo(text='Initializing...', spinner='dots')
        spinner.start()

    try:
        # We must process chronologically to handle sessions crossing midnight
        dates_to_process = [today - timedelta(days=i) for i in range(days_back - 1, -1, -1)]

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

    # --- Print Summaries ---
    print("\n--- Daily Screen Time Summary ---")
    if not daily_hourly_durations:
        print("No screen time data found for the selected period.")
        return

    sorted_days = sorted(daily_hourly_durations.keys())
    for day in sorted_days:
        print_hourly_breakdown(day, daily_hourly_durations[day], daily_block_durations[day])

    # --- Monthly Summary ---
    print("\n--- Monthly Summary ---")
    for day_data in daily_hourly_durations.values():
        total_actual_hours += sum(day_data.values(), timedelta()).total_seconds() / 3600
    
    total_block_hours = sum([d.total_seconds() for d in daily_block_durations.values()]) / 3600
    total_expected_hours = len(days_with_activity) * EXPECTED_HOURS_PER_DAY
    
    if total_expected_hours > 0:
        monthly_raw_percentage = (total_actual_hours / total_expected_hours) * 100
        monthly_block_percentage = (total_block_hours / total_expected_hours) * 100
        
        raw_str = f"Raw: {total_actual_hours:.1f} h ({monthly_raw_percentage:.0f}%)"
        block_str = f"Block: {total_block_hours:.1f} h ({monthly_block_percentage:.0f}%)"
        
        print(f"Total for {len(days_with_activity)} active day(s): {raw_str:<22}{block_str}")
    else:
        print("No activity to summarize.")


def main():
    """
    Main function to run the CLI.
    """
    parser = argparse.ArgumentParser(description="A simple to use time tracking CLI for macOS.")
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
    args = parser.parse_args()

    if args.clear_cache:
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
            print(f"Cache file '{DB_FILE}' has been deleted.")
        else:
            print("No cache file to delete.")
        return

    get_screen_time(args.days, args.verbose, args.no_cache)


if __name__ == "__main__":
    main()

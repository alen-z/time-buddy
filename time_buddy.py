import json
import subprocess
from datetime import datetime, timedelta, date
import argparse
from collections import defaultdict

EXPECTED_HOURS_PER_DAY = 7.5


def print_hourly_breakdown(day: date, hourly_durations: defaultdict):
    """Prints a single line of 24 colored blocks representing a day's screen time."""
    # --- Color gradient (10 steps from red to green in ANSI 256-color) ---
    gradient_colors = [196, 202, 208, 214, 220, 226, 190, 154, 118, 46]
    
    total_duration = sum(hourly_durations.values(), timedelta())
    total_hours = total_duration.total_seconds() / 3600
    percentage = (total_hours / EXPECTED_HOURS_PER_DAY) * 100

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
    
    output_line += f"  {total_hours:.1f} hours ({percentage:.0f}%)"
    print(output_line)


def get_screen_time(days_back):
    """
    Calculates screen time for the last N days, fetching logs day by day.
    """
    daily_hourly_durations = {}
    today = datetime.now().date()
    total_actual_hours = 0
    days_with_activity = set()

    for i in range(days_back):
        current_day = today - timedelta(days=i)
        start_of_day = datetime.combine(current_day, datetime.min.time())
        end_of_day = datetime.combine(current_day, datetime.max.time())
        
        predicate = 'process == "loginwindow" and eventMessage contains "com.apple.sessionagent.screenIs"'
        command = [
            'log', 'show', '--style', 'json',
            '--predicate', predicate,
            '--start', start_of_day.strftime('%Y-%m-%d %H:%M:%S'),
            '--end', end_of_day.strftime('%Y-%m-%d %H:%M:%S')
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            logs = json.loads(result.stdout)
            
            if not logs:
                continue

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
                
                message = entry.get("eventMessage", "")
                
                if "screenIsUnlocked" in message:
                    events.append({'timestamp': timestamp, 'type': 'unlocked'})
                elif "screenIsLocked" in message:
                    events.append({'timestamp': timestamp, 'type': 'locked'})
            
            events.sort(key=lambda x: x['timestamp'])
            
            hourly_durations = defaultdict(timedelta)
            unlock_time = None

            for event in events:
                if event['type'] == 'unlocked':
                    if unlock_time is None:
                        unlock_time = event['timestamp']
                elif event['type'] == 'locked':
                    if unlock_time is not None:
                        lock_time = event['timestamp']
                        
                        current_time = unlock_time
                        while current_time < lock_time:
                            current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
                            next_hour_start = current_hour_start + timedelta(hours=1)
                            
                            segment_end = min(lock_time, next_hour_start)
                            duration_in_hour = segment_end - current_time
                            
                            hourly_durations[current_time.hour] += duration_in_hour
                            
                            current_time = next_hour_start
                            
                        unlock_time = None
            
            if any(duration.total_seconds() > 0 for duration in hourly_durations.values()):
                daily_hourly_durations[current_day] = hourly_durations
                days_with_activity.add(current_day)

        except subprocess.CalledProcessError as e:
            if e.returncode == 1 and not e.stdout and not e.stderr:
                pass  # No logs found, continue silently
            else:
                print(f"Error executing log command for {current_day.isoformat()}: {e}")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from log output for {current_day.isoformat()}.")
        except Exception as e:
            print(f"An unexpected error occurred for {current_day.isoformat()}: {e}")

    print("\n--- Daily Screen Time Summary ---")
    if not daily_hourly_durations:
        print("No screen time data found for the selected period.")
        return

    sorted_days = sorted(daily_hourly_durations.keys())
    for day in sorted_days:
        print_hourly_breakdown(day, daily_hourly_durations[day])

    # --- Monthly Summary ---
    print("\n--- Monthly Summary ---")
    for day_data in daily_hourly_durations.values():
        total_actual_hours += sum(day_data.values(), timedelta()).total_seconds() / 3600
    
    total_expected_hours = len(days_with_activity) * EXPECTED_HOURS_PER_DAY
    
    if total_expected_hours > 0:
        monthly_percentage = (total_actual_hours / total_expected_hours) * 100
        print(f"Total for {len(days_with_activity)} active day(s): {total_actual_hours:.1f} / {total_expected_hours:.1f} hours ({monthly_percentage:.0f}%)")
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
    args = parser.parse_args()

    get_screen_time(args.days)


if __name__ == "__main__":
    main()

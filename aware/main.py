import argparse
import json
import math
import subprocess
from datetime import datetime, timedelta, timezone

import boto3
import yaml


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def build_queue_name(config, key):
    prefix = config.get('queue_prefix', '')
    suffix = config.get('queue_suffix', '')
    name = f"{prefix}{key}{suffix}"
    if config.get('fifo'):
        name += '.fifo'
    return name


def lowest_gap_over_threshold(sends, min_gap_hours):
    min_gap = timedelta(hours=min_gap_hours)
    gaps = [(sends[i][0] - sends[i - 1][0], sends[i - 1][0], sends[i][0])
            for i in range(1, len(sends))]
    big = [g for g in gaps if g[0] > min_gap]
    return min(big, key=lambda x: x[0]) if big else None


def get_sends(cloudwatch, queue_name, lookback_minutes):
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=lookback_minutes)

    response = cloudwatch.get_metric_data(
        MetricDataQueries=[{
            'Id': 'sent',
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/SQS',
                    'MetricName': 'NumberOfMessagesSent',
                    'Dimensions': [{'Name': 'QueueName', 'Value': queue_name}],
                },
                'Period': 60,
                'Stat': 'Sum',
            },
            'ReturnData': True,
        }],
        StartTime=start,
        EndTime=now,
    )

    result = response['MetricDataResults'][0]
    sends = [(t, v) for t, v in zip(result['Timestamps'], result['Values']) if v > 0]
    sends.sort(key=lambda x: x[0])
    return sends


def status_emoji(gap_hours, goal_hours):
    if gap_hours is None or goal_hours is None:
        return "❓"
    if goal_hours - 2 <= gap_hours <= goal_hours + 1:
        return "✅"
    return "⚠️"


def nomad_job_name(config, item):
    if item.get('nomad_job'):
        return item['nomad_job']
    template = config.get('nomad_job_template')
    if template:
        return template.format(key=item['key'])
    return None


def get_nomad_allocs(job_name):
    if job_name is None:
        return None
    result = subprocess.run(
        ["nomad", "job", "inspect", job_name],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return data["Job"]["TaskGroups"][0]["Count"]


def main():
    parser = argparse.ArgumentParser(description='Find longest idle gap between SQS message sends')
    parser.add_argument('-c', '--config', default='queues.yaml')
    parser.add_argument('-p', '--profile', help='AWS profile name')
    parser.add_argument('-l', '--lookback', type=int, default=2880, help='Lookback period in minutes')
    args = parser.parse_args()

    config = load_config(args.config)
    min_gap_hours = config.get('min_gap_hours', 2.0)
    session = boto3.Session(profile_name=args.profile)
    region = config.get('region', 'us-east-1')
    cw = session.client('cloudwatch', region_name=region)

    rows = []
    for item in config['items']:
        key = item['key']
        goal = item.get('expected_hours')
        queue_name = build_queue_name(config, key)
        sends = get_sends(cw, queue_name, args.lookback)

        gap_hours = None
        if len(sends) < 2:
            gap_str = "N/A"
        else:
            result = lowest_gap_over_threshold(sends, min_gap_hours)
            if result is None:
                gap_str = "N/A"
            else:
                delta, _, _ = result
                gap_hours = delta.total_seconds() / 3600
                gap_str = f"{gap_hours:.1f}h"

        current_allocs = get_nomad_allocs(nomad_job_name(config, item))
        alloc_str = str(current_allocs) if current_allocs is not None else "N/A"

        ideal = None
        if gap_hours is not None and goal is not None and current_allocs is not None:
            ratio = gap_hours / goal
            if ratio > 1:
                ideal = math.ceil(current_allocs * ratio)
            else:
                ideal = max(1, round(current_allocs * ratio))
        ideal_str = str(ideal) if ideal is not None and ideal != current_allocs else ("-" if current_allocs is not None else "N/A")

        goal_str = f"{goal:.0f}h" if goal is not None else "-"
        emoji = status_emoji(gap_hours, goal)
        rows.append((key.upper(), gap_str, goal_str, alloc_str, ideal_str, emoji, current_allocs, ideal))

    key_width = max(len(r[0]) for r in rows)
    gw = 8
    print(f"{'KEY'.ljust(key_width)}  {'Gap'.ljust(gw)}  {'Goal'.ljust(gw)}  {'Alloc'.ljust(6)}  {'Ideal'.ljust(6)}  OK?")
    print(f"{'-' * key_width}  {'-' * gw}  {'-' * gw}  {'-' * 6}  {'-' * 6}  ---")
    total_alloc = 0
    total_ideal = 0
    for key, gap, goal, alloc, ideal, emoji, raw_alloc, raw_ideal in rows:
        print(f"{key.ljust(key_width)}  {gap.ljust(gw)}  {goal.ljust(gw)}  {alloc.ljust(6)}  {ideal.ljust(6)}  {emoji}")
        if raw_alloc is not None:
            total_alloc += raw_alloc
            total_ideal += raw_ideal if raw_ideal is not None else raw_alloc

    print()
    print(f"{'Total'.ljust(key_width)}  {'':{gw}}  {'':{gw}}  {str(total_alloc).ljust(6)}  {str(total_ideal).ljust(6)}")


if __name__ == '__main__':
    main()
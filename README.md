<p align="center">
  <img src="assets/logo.svg" alt="Aware logo: minimalist shining third eye" width="160">
</p>

# Aware

## Purpose

Aware monitors SQS queues and reports how well each one's worker capacity keeps up with
incoming traffic. For every configured item it:

1. Pulls `NumberOfMessagesSent` from CloudWatch over a lookback window.
2. Finds the longest idle gap (period with no sends) above a configurable threshold.
3. Compares that gap against the item's expected processing time.
4. Reads the current Nomad allocation count for the item's job.
5. Prints the current vs. ideal allocation count, so you can see where capacity needs
   scaling up or can be scaled down.

It was originally written for country-based jobs but is fully generic: the queue name
parts, Nomad job names, region, and thresholds are all driven by `queues.yaml`.

## Requirements

- Python 3
- AWS credentials configured (see [Setup credentials](#setup-credentials)) with access to:
  - CloudWatch `AWS/SQS` metrics for the queues
  - the target region
- (Optional) `nomad` CLI installed and connected to the cluster. Without it,
  allocation columns show `N/A`.

## Setup

### Virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Setup credentials

Point the tool at an AWS account/profile via a standard credential source, e.g. a
named profile in `~/.aws/credentials`, and pass it with `-p`:

```bash
aws configure --profile my-aws-profile
```

With no `-p` flag, the default AWS credential chain is used.

## Configuration

`queues.yaml` is git-ignored, so create it locally. Minimal example:

```yaml
queue_prefix: "queue_"
fifo: true
region: us-east-1
nomad_job_template: "job-{key}"
items:
  - key: ar
    expected_hours: 16
  - key: br
    expected_hours: 12
```

### Keys

Top-level:

- `queue_prefix` (optional): prepended to every SQS queue name.
- `queue_suffix` (optional): appended to every SQS queue name.
- `fifo` (optional): when `true`, appends `.fifo` as the final queue-name suffix
  (SQS FIFO queues).
- `region`: AWS region used for CloudWatch queries.
- `nomad_job_template` (optional): Nomad job name pattern; `{key}` is replaced with the
  item key. Omit to skip allocation lookups.
- `min_gap_hours` (optional): ignore gaps shorter than this. Default `2`.
- `items`: the list to monitor.

Per item:

- `key`: unique identifier used to build the queue and Nomad job names.
- `expected_hours`: expected processing time in hours (the goal).
- `nomad_job` (optional): explicit Nomad job name for this item; overrides
  `nomad_job_template`.

The SQS queue name is built as `queue_prefix + key + queue_suffix`, plus `.fifo` when
`fifo` is enabled. For the example above, item `ar` maps to queue
`queue_ar.fifo` and Nomad job `job-ar`.

## Usage

```bash
python -m aware.main -c queues.yaml -p my-aws-profile -l 2880
```

### Options

- `-c, --config`: path to the config file (default `queues.yaml`)
- `-p, --profile`: AWS profile name for the session
- `-l, --lookback`: CloudWatch lookback window in minutes (default `2880`)

### Nomad

Allocation counts are read locally by calling `nomad job inspect <job-name>` and parsing
the JSON response (`Job.TaskGroups[0].Count`). The command must therefore be run from a
machine with access to the Nomad cluster. Jobs configured via `nomad_job_template` or a
per-item `nomad_job`; items without a job name show `N/A` in the allocation columns.

## Output

Example:

```
KEY   Gap      Goal     Alloc   Ideal   OK?
---   ------   ------   ------  ------  ---
AR    13.2h    16h      4       3       ✅
BR    9.1h     12h      6       5       ✅
CL    20.5h    12h      6       10      ⚠️
...
Total                         42      41
```

- `KEY`: item key (uppercased).
- `Gap`: longest idle gap above `min_gap_hours` in the lookback window, or `N/A`
  when fewer than two sends or no gap above the threshold were found.
- `Goal`: the item's `expected_hours`.
- `Alloc`: current Nomad allocation count, or `N/A` if no job is configured/lookup failed.
- `Ideal`: estimated allocations needed, scaled by the `Gap / Goal` ratio; `-` when it
  matches current, `N/A` when allocations aren't known.
- `OK?`: `✅` when the gap is within tolerance (`Goal - 2` to `Goal + 1` hours),
  `⚠️` otherwise, `❓` when data is missing.

## Troubleshooting

- **`N/A` everywhere / `KeyError: 'items'`**: the config file is missing or uses an old
  schema. Check that `queues.yaml` has an `items` list.
- **Allocations are `N/A`**: `nomad` CLI is not installed, not connected, or no
  `nomad_job_template`/`nomad_job` is configured for the item. Verify with
  `nomad job inspect <job>`.
- **CloudWatch errors**: confirm the `-p` profile has CloudWatch read access and that
  `region` matches where the queues live.
- **All gaps `N/A`**: the lookback window (`-l`) may be too short, or no queued traffic
  occurred; increase the lookback.

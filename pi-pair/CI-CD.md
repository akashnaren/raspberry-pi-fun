# CI/CD

Pi 0.2 High checks and the pi3 sync. GitHub Actions on `ubuntu-latest` only. No Jenkins and no paid runners.

Two GitHub Environments keep pull-request checks away from the deploy key.

## development

`.github/workflows/ci.yml` binds every job to the environment named **development**.

It runs on:

- every pull request into `main`
- every push to a branch other than `main`
- **Run workflow** (`workflow_dispatch`) from the Actions tab

| Job | What it does |
| --- | --- |
| lint | `ruff==0.13.2` on `pi-pair`, then shellcheck on `install.sh`, `start.sh`, `mesh-hello.sh`, and `ci/deploy_pi3.sh` |
| unit | `python3 -m unittest discover -s pi-pair -p 'test_*.py'` — router, health-cache TTL, data-stack negatives, deploy fail-closed |
| data-stack | `pi-pair/ci/validate_data_stack.py` on `pi-pair/data`, then uploads the artifact `data-stack-validation-report` |

Leave **required reviewers off** on development. A reviewer rule makes every PR check wait for a person to approve the environment, and the suite looks hung. This environment does not need secrets. Do not store `PI3_SSH_KEY` here.

## production

`.github/workflows/cd.yml` binds the deploy job to the environment named **production**.

It runs on:

- a push to `main` (that is the merge)
- **Run workflow** when the selected branch is `main`

A dispatch from any other branch skips the job (`github.ref == refs/heads/main`).

On main the job:

1. Validates `pi-pair/data` again. A bad fixture never reaches SSH. The same validation report artifact is uploaded.
2. Requires the production secrets below. An empty required secret fails the job. The script does not generate a key, and it does not fall back to a key on disk.
3. `rsync`s `pi-pair/` to pi3. A push to `main` is a real sync. The workflow_dispatch input **dry_run** adds `rsync -n` (no writes) and still requires the secrets.

Required reviewers on production are optional. Add them in **Settings → Environments → production** if you want a person to approve each deploy. Leave them empty and a merge to main syncs as soon as the secrets exist.

The first CD run after secrets are missing fails on purpose. Fill the secrets, then re-run. Do not treat that failure as a skipped deploy.

## What lands on pi3

pi3 is the dataset host: canned map, seed files, and (later, on the device) the train queue. This sync does not start training and does not pull a model.

| Synced | Left alone |
| --- | --- |
| Router code, static UI, `data/canned`, `data/seed` (SFT, preference, held-out eval, schemas), the dataset registry | `peers.json` on the Pi |
| | `data/shards/`, `data/train/`, `data/prepared/`, `adapters/` |
| | `*.gguf`, `*.ggml`, and `ci/fixtures/` (the broken negative fixture) |

Remote directory defaults to `pi-pair` under the SSH user's home (`~/pi-pair`). The last path segment must be `pi-pair`, so `--delete` cannot wipe a home directory. `rsync --delete` applies only inside that directory, and excluded paths are not deleted, so a later train queue under `data/shards/` survives a deploy.

CD does **not** run `install.sh` and does **not** restart systemd. `install.sh` pulls an Ollama model; running it from this job would put chat weights on pi3. First-time unit setup stays a manual step on the Pi. After a router change, restart the user service on the Pi yourself.

Generation stays on pi4. Health stays on pi2. The script refuses a host whose name contains `pi2` or `pi4`, and it refuses the documented LAN addresses `10.0.0.180` (pi2) and `10.0.0.166` (pi4).

## Secrets and variables

Create these on the **production** environment (**Settings → Environments → production → Environment secrets**).

| Name | Kind | Required | Value |
| --- | --- | --- | --- |
| `PI3_SSH_HOST` | secret | yes | pi3. Tailscale name `rpi-pi3`, or another address the GitHub runner can open SSH to. Not pi2. Not pi4. |
| `PI3_SSH_USER` | secret | yes | SSH user on pi3. |
| `PI3_SSH_KEY` | secret | yes | Private key **text**, including the `BEGIN` and `END` lines. Not a file path. |
| `PI3_SSH_PORT` | secret | no | TCP port. Unset or empty means 22. |
| `PI3_PAIR_DIR` | variable | no | Remote directory. Unset means `pi-pair`. |

`ubuntu-latest` is not on the tailnet. `rpi-pi3` resolves only inside Tailscale. If the runner cannot route to the host, `ssh-keyscan` fails and the job stops. Host-key checking stays on for that scan (`StrictHostKeyChecking=yes`). The workflow does not take a Tailscale auth key. Add network reachability separately if the Pi is tailnet-only; do not disable the key check to make CD green.

Each deploy keyscans the host and then requires that scan's key. That rejects an empty scan. It does not remember a key from a previous run, because the runner disk is new each time.

## How to re-run

- Pull request: open the check in the Actions tab and choose **Re-run failed jobs**. A new push to the PR branch also runs CI.
- CI on a branch with no PR: **Actions → CI → Run workflow** and pick the branch. That uses **development**.
- Deploy: **Actions → CD → Run workflow**, branch **main**. Check **dry_run** for `rsync -n`. Leave it unchecked to sync. Re-running the workflow a merge started syncs again. A second sync is safe; rsync is idempotent.
- On a laptop:

```bash
python3 -m unittest discover -s pi-pair -p 'test_*.py'
python3 pi-pair/ci/validate_data_stack.py --data pi-pair/data --report /tmp/data-stack-report.json
bash pi-pair/ci/deploy_pi3.sh --plan
```

After `PI3_SSH_HOST`, `PI3_SSH_USER`, and `PI3_SSH_KEY` are exported:

```bash
bash pi-pair/ci/deploy_pi3.sh --dry-run
bash pi-pair/ci/deploy_pi3.sh
```

Negative check (this must exit 1, error `eval_overlaps_canned`):

```bash
python3 pi-pair/ci/validate_data_stack.py --data pi-pair/ci/fixtures/broken_overlap
```

## How to add a check

1. Router or cache behavior: add a `test_*` method in `pi-pair/test_*.py` or `pi-pair/ci/test_*.py`. The unit job discovers it. No workflow edit.
2. Data rule: change `pi-pair/ci/validate_data_stack.py`, keep `pi-pair/data` valid, and add a failing case (a temp copy in the unit test, or a tree under `pi-pair/ci/fixtures/`). The data-stack job runs the validator. The unit job runs the negative test.
3. A new CI job: add it to `.github/workflows/ci.yml` with `runs-on: ubuntu-latest` and `environment: development`.
4. A new dataset file: add the JSONL under `data/seed/` (or `data/canned/` for the map), a schema, the three `dataset_info.json` registries, the `EXPECTED` row in the validator, and the count in `BUILD_MANIFEST.json`. CI stays red until those agree. Held-out `input` strings must not match canned `input` strings. Counts stay the checked-in seed sizes.

## Role locks the pipeline keeps

`BUILD_MANIFEST.json` must say:

- `generate` is `["pi4"]`
- `dataset_and_train` is `["pi3"]`
- `health` is `["pi2"]`
- `train_then_delete` is true
- `weak_gen` is false
- `synthetic` is true and `pii` is false

Eval inputs are folded (case and whitespace) and compared to canned inputs. Overlap fails the data-stack job. The committed proof is `pi-pair/ci/fixtures/broken_overlap/`.

## Creating the environments

These two environments were **not** created from this change. `PUT /repos/akashnaren/raspberry-pi-fun/environments/{development,production}` returned **403** `Resource not accessible by integration` (the token used here cannot administer environments). A follow-up list showed `total_count: 0`.

GitHub may create an environment the first time a workflow job names it, if Actions is allowed to. If a job instead stops because the environment is missing, a repo admin runs the commands below (no reviewers, no wait timer):

```bash
gh api --method PUT -H "Accept: application/vnd.github+json" \
  /repos/akashnaren/raspberry-pi-fun/environments/development \
  -f wait_timer=0

gh api --method PUT -H "Accept: application/vnd.github+json" \
  /repos/akashnaren/raspberry-pi-fun/environments/production \
  -f wait_timer=0
```

Production reviewers stay a UI choice after the environment exists. Do not add reviewers to development. A reviewer on development makes every pull-request check wait for a person.

# CI/CD

Pi GPT 1.0 checks and the pi3 sync. GitHub Actions on `ubuntu-latest` only. No Jenkins and no paid runners.

Two GitHub Environments keep pull-request checks away from the deploy key.

## development

`.github/workflows/ci.yml` binds every job to the environment named **development**.

It runs on:

- every pull request into `main`
- every push to a branch other than `main`
- **Run workflow** (`workflow_dispatch`) from the Actions tab

| Job | What it does |
| --- | --- |
| lint | `ruff==0.13.2` check and `ruff format --check` on `pi-pair`, `npm run lint` and Biome (`npm run lint:js`) on `pi-pair/web`, then shellcheck on `install.sh`, `start.sh`, `mesh-hello.sh`, and `ci/deploy_pi3.sh` |
| unit | `npm run build` and `npm test` in `pi-pair/web`, then `git diff --exit-code -- pi-pair/static`, then `python3 -m unittest discover -s pi-pair/tests -t pi-pair` |
| data-stack | `pi-pair/ci/validate_data_stack.py` on `pi-pair/data`, then uploads the artifact `data-stack-validation-report` |

Leave **required reviewers off** on development. A reviewer rule makes every PR check wait for a person to approve the environment, and the suite looks hung. This environment does not need secrets. Do not store `PI3_SSH_KEY` here.

## production

`.github/workflows/cd.yml` binds the deploy job to the environment named **production**.

It runs on:

- a push to `main` (that is the merge)
- **Run workflow** when the selected branch is `main`

A dispatch from any other branch skips the job (`github.ref == refs/heads/main`).

On main the job:

1. Validates `pi-pair/data` again. A bad fixture never reaches the tailnet. The same validation report artifact is uploaded.
2. Requires `TS_AUTHKEY`, `PI3_SSH_HOST`, `PI3_SSH_USER`, and `PI3_SSH_KEY`. An empty required secret fails the job before Tailscale starts. The workflow does not generate a key, and it does not fall back to a key on disk.
3. Joins the tailnet with the official action `tailscale/github-action` at `v4.2.0` (`d1b6cd204f8dceda5b3eaad7f1f767be390056cd`), Tailscale client `1.94.2`, using production secret `TS_AUTHKEY`. The ephemeral node hostname is `gh-cd-<run id>`.
4. `rsync`s `pi-pair/` to pi3 over that tailnet. A push to `main` is a real sync. The workflow_dispatch input **dry_run** adds `rsync -n` (no writes) and still requires the secrets. pi2 and pi4 hosts are still refused.
5. When the job ends, the action's post step logs out of Tailscale and stops `tailscaled`. A logout failure is a warning, so the job result stays the deploy result. An ephemeral node is removed by Tailscale if that logout misses.

Required reviewers on production are optional. Add them in **Settings → Environments → production** if you want a person to approve each deploy. Leave them empty and a merge to main joins the tailnet and syncs once `TS_AUTHKEY` is set beside the SSH secrets.

If `TS_AUTHKEY` is empty, CD fails before Tailscale starts. Fill that secret, then re-run. Do not treat that failure as a skipped deploy.

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
| `TS_AUTHKEY` | secret | yes | Reusable, ephemeral Tailscale auth key tagged `tag:ci`. Never commit it. |
| `PI3_SSH_HOST` | secret | yes | pi3 MagicDNS name `rpi-pi3`. Not pi2. Not pi4. Resolved only after the runner joins the tailnet. |
| `PI3_SSH_USER` | secret | yes | SSH user on pi3. |
| `PI3_SSH_KEY` | secret | yes | Private key **text**, including the `BEGIN` and `END` lines. Not a file path. |
| `PI3_SSH_PORT` | secret | no | TCP port. Unset or empty means 22. |
| `PI3_PAIR_DIR` | variable | no | Remote directory. Unset means `pi-pair`. |

## Tailscale auth key

`ubuntu-latest` is not on the tailnet. CD joins before SSH, using `authkey: ${{ secrets.TS_AUTHKEY }}`. The key never belongs in git, in a pull request, or on the **development** environment.

Create it in the Tailscale admin console (Keys / Trust credentials):

1. Define `tag:ci` in the tailnet policy, owned by whoever will mint the key.
2. Allow `tag:ci` to open SSH to pi3 (`rpi-pi3` on port 22, or whatever `PI3_SSH_PORT` is). That grant is in addition to the existing policy.
3. Generate an auth key that is **reusable**, **ephemeral**, and tagged **`tag:ci`**. If device approval is on, make the key pre-approved.
4. Store the key as the **production** environment secret named exactly `TS_AUTHKEY`.

Reusable means later CD runs can use the same secret. Ephemeral means the GitHub runner's node is removed when it logs out or drops offline, so CI nodes do not pile up next to the Pis. The tag lives on the key. This workflow does not pass a `tags:` input, because that input is for an OAuth client.

Tailscale's action marks `authkey` as deprecated in favor of an OAuth client (`oauth-client-id`, `oauth-secret`, and `tags: tag:ci`). This job uses `TS_AUTHKEY` so there is one secret to set. Do not commit either kind of credential.

The pinned action is `tailscale/github-action@d1b6cd204f8dceda5b3eaad7f1f767be390056cd` (tag `v4.2.0`). The client version in the workflow is `1.94.2`, that release's default. Its post step runs `sudo tailscale logout`, then stops `tailscaled` and runs `tailscaled --cleanup`. That is best-effort: a cleanup error is a warning and does not flip a green deploy to red.

After the runner is on the tailnet, `deploy_pi3.sh` is unchanged: it still requires the SSH secrets, still refuses pi2 and pi4 (including `10.0.0.180` and `10.0.0.166`), and still keyscans with `StrictHostKeyChecking=yes`. Set `PI3_SSH_HOST` to `rpi-pi3`. A LAN address is not reachable from the runner.

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

## Environments

`development` and `production` both exist on this repository. Software set `PI3_SSH_HOST`, `PI3_SSH_USER`, and `PI3_SSH_KEY` on **production**. `PI3_SSH_PORT` stays optional. `TS_AUTHKEY` is the remaining production secret; add it with the steps above. Do not add reviewers to development. Production reviewers stay a UI choice.

## What a push to main does

Pull requests into `main` use the GitHub Environment `development` (lint, unit tests, data-stack validation). A push to `main` uses the GitHub Environment `production`, joins Tailscale with `TS_AUTHKEY`, and syncs this tree plus `data/canned` to pi3. Names of the secrets, the dry-run input, and how to add a check are in the sections above.

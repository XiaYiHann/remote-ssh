---
name: remote-ssh
description: Manage SZU AI Cloud GPU debug jobs and remote SSH connections. Subscribe compute, deploy tools, and run remote commands.
---

## Remote SSH Access

When the user needs remote GPU compute (debug jobs, experiments, distributed training, or more local compute):

### 1. Estimate resource need

Inspect the project context to choose the smallest suitable resource:

| Workload clue | Default choice |
|---|---|
| debugging, smoke tests, small PyTorch experiments, 24 GB is enough | single RTX 4090, 1 hour |
| one large model or memory pressure above 24 GB | single H100/H200 or the smallest higher-memory GPU, 1-2 hours |
| distributed training, FSDP/DeepSpeed, explicit multi-GPU, batch experiments | H100/H200 multi-card with the minimum card count, duration from expected run time |
| unknown resource availability | run `remote-ssh list-resources` first, then choose by `--gpu` keyword and `--card-num` |

For costly jobs such as multi-card H200 or long durations, prefer an explicit user confirmation unless the user already specified the capacity/duration or asked the agent to decide and proceed. Always avoid rapid repeated submissions; after a failure, inspect the API error and retry only once with a justified adjustment.

### 2. Credentials

The CLI reads credentials from environment variables or `~/.bashrc` exports:

- `SZU_AICLOUD_USERNAME`
- `SZU_AICLOUD_PASSWORD`
- `SZU_AICLOUD_PROXY` (optional)
- `SZU_AICLOUD_KEY_PATH` (optional, defaults to `~/.ssh/id_rsa`)

If the password is missing, ask the user. Never print passwords or tokens.

### 3. Inspect available resources when needed

```bash
remote-ssh list-resources
```

### 4. Create or query a debug job

Dry-run first to verify the resolved payload:

```bash
remote-ssh create --gpu 4090 --card-num 1 --duration-hours 1 --job-name "<project>-debug-$(date +%Y%m%d-%H%M%S)"
```

When the user intent is operational, submit and wait for SSH readiness:

```bash
remote-ssh create --submit --wait-ssh --gpu 4090 --card-num 1 --duration-hours 1 --job-name "<project>-debug-$(date +%Y%m%d-%H%M%S)"
```

The CLI auto-detects and mounts the current team's file storage unless
overridden. To match the web UI's “文件存储” selection flow, leave
`--storage-from` and `--mount-to` unset; it will use the detected file-storage
path as the container mount path. Use `--mount-to` only for an explicit
container mount path.

High-memory multi-GPU examples:

```bash
remote-ssh create --submit --wait-ssh --gpu H100 --card-num 1 --duration-hours 2 --job-name "<project>-debug-$(date +%Y%m%d-%H%M%S)"
remote-ssh create --submit --wait-ssh --gpu H100 --card-num 4 --duration-hours 4 --job-name "<project>-debug-$(date +%Y%m%d-%H%M%S)"
remote-ssh create --submit --wait-ssh --gpu H200 --card-num 4 --duration-hours 4 --job-name "<project>-debug-$(date +%Y%m%d-%H%M%S)"
```

### 5. Query SSH for an existing job

If there is already an active suitable job, reuse it instead of renting another one:

```bash
remote-ssh ssh <job_id> --wait-ssh
```

### 6. Verify the remote host

After resolving SSH commands, verify connectivity before reporting success:

```bash
timeout 30 ssh -i ~/.ssh/id_rsa \
  -o BatchMode=yes \
  -o ConnectTimeout=10 \
  -o StrictHostKeyChecking=accept-new \
  -o ProxyCommand='nc -X 5 -x <proxy_ip>:<port> %h %p' \
  root@<job_id> 'echo OK && hostname && whoami && nvidia-smi -L'
```

Prefer IP-based proxy commands for local execution. If the CLI prints both a domain proxy and a runtime `sshUrl` IP, use the IP plus the original proxy port to avoid TUN/DNS issues.

### 7. Optional reverse proxy tunnel

If the remote job has no public internet but the local machine has a working
HTTP/SOCKS proxy, prefer an SSH reverse tunnel before building large offline
bundles. Bind only to remote loopback so the proxy is not exposed to other
machines:

```bash
ssh -N -f \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -R 127.0.0.1:17890:127.0.0.1:7890 \
  <HOST>
```

This maps remote `127.0.0.1:17890` to the local proxy at `127.0.0.1:7890`.
Adjust the local port if the user's proxy listens elsewhere. On the remote,
export proxy variables according to the local proxy protocol:

```bash
export http_proxy=http://127.0.0.1:17890
export https_proxy=http://127.0.0.1:17890
export HTTP_PROXY=$http_proxy
export HTTPS_PROXY=$https_proxy
```

For SOCKS-only proxies, use:

```bash
export all_proxy=socks5h://127.0.0.1:17890
export ALL_PROXY=$all_proxy
```

Verify the tunnel before relying on it:

```bash
ssh <HOST> 'curl -I --max-time 10 https://huggingface.co || curl -I --max-time 10 https://github.com'
```

If reverse forwarding is disabled, the local proxy port is wrong, or the test
fails, fall back to the local-prepare-and-transfer workflow below.

### 8. Probe remote environment, then prepare locally

Assume SZU AI Cloud debug jobs do not have public internet access. Do not start
by running remote `wget`, `curl`, `git clone`, `pip install`, `apt`, `hf
download`, or model download commands unless remote outbound network access has
been explicitly verified.

Before installing anything, run a remote preflight over SSH and identify the
minimum missing environment:

```bash
ssh <HOST> 'set -eu
echo "== system =="; uname -a; whoami; pwd
echo "== gpu =="; command -v nvidia-smi >/dev/null && nvidia-smi -L || true
echo "== tools =="; for x in python python3 pip pip3 uv conda git rsync tar unzip gcc g++ make cmake nvcc; do command -v "$x" >/dev/null && printf "%s %s\n" "$x" "$(command -v "$x")" || printf "%s MISSING\n" "$x"; done
echo "== python =="; python3 --version 2>/dev/null || true; python3 -m pip --version 2>/dev/null || true
echo "== cuda =="; nvcc --version 2>/dev/null || true
echo "== mounts =="; df -h; mount | grep -E "/share|workspace|home" || true
'
```

Then prepare only what is missing on the local machine and transfer it over SSH.
Use this rule for Python wheels, Conda/env archives, source archives, model
weights, datasets, binaries, license files, and large config bundles:

1. Build or download missing artifacts locally, using local internet/proxy.
2. Prefer portable artifacts: `pip download -d wheelhouse ...`, `uv pip
   compile` plus a wheelhouse, `conda-pack`, tarballs, model snapshots, or
   checked-out source archives.
3. Verify local files exist and, for large artifacts, record size or checksum
   when practical.
4. Transfer with `rsync -avP`, `scp`, or `ssh <HOST> "cat > <remote-path>" <
   <local-file>`.
5. Install on the remote from local files only, for example `pip install
   --no-index --find-links <wheelhouse> -r requirements.txt`, unpack a
   `conda-pack` archive, or point model code at the copied model directory.
6. Put persistent assets under the mounted file-storage path when they must
   survive job termination; use `/root`, `/tmp`, or `/workspace` only for
   disposable runtime state unless those paths are backed by the mount.

When reporting a remote setup plan, include: preflight findings, missing
components, local artifacts prepared, transfer destination, and remote install
commands.

### 9. Report to user

Final response should include:
- task id and task name
- selected GPU profile and duration
- verified SSH command
- verification result
- expiry or configured duration

### 10. Tool deployment

When the user mentions `bita` or provides a bita download URL:

1. Download or locate the binary locally. `/tmp` may be `noexec`; install executables under `~/bin` or `~/.local/bin`.
2. Sync to remote if needed:
   ```bash
   ssh <HOST> "cat > /root/bin/bita" < ~/bin/bita
   ssh <HOST> "chmod +x /root/bin/bita"
   ```
3. Configure or verify login:
   ```bash
   ~/bin/bita login -u <user> -p <password> -t <tenant> -e https://console.aicloud.szu.edu.cn
   ~/bin/bita apply --help
   ```

Prefer the CLI for SZU AI Cloud debug-job creation unless the user explicitly asks for `bita`.

## Edge Cases

- If Playwright is missing, install project/runtime dependencies only after checking the local Python environment.
- If login fails, open a headed browser once to inspect captcha, risk-control, or expired credentials.
- If API submission returns validation errors, map the error to backend IDs (`imageId`, `powerConfId`, `sshId`, `teamId`) rather than retrying UI clicks.
- If SSH is not ready, poll the debug-url endpoint with `--wait-ssh`; do not repeatedly submit new jobs.
- If `nc -X 5 -x` fails because the local netcat is not OpenBSD netcat, use the Windows/ncat form printed by the CLI.
- If a job is time-limited, include the absolute expiry estimate in the final answer when start time or configured duration is known.

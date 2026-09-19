# Windows local operation and start at login

This is an operator procedure, not an automatic installer. Creating a scheduled
task is an explicit deployment action. The commands below have not been used to
register or start a task during development. Keep real Telegram acceptance
separate from automated tests.

## 1. Foreground preflight

Use the same ordinary Windows account that owns the vault and authorized OAuth
tokens. Do not use SYSTEM or highest privileges. In PowerShell:

```powershell
$stewardProject = 'C:\Users\clare\OneDrive\Desktop\steward'
$stewardExecutable = Join-Path $stewardProject '.venv\Scripts\steward.exe'
if (-not (Test-Path -LiteralPath $stewardExecutable -PathType Leaf)) {
    throw 'Steward virtual-environment entry point is missing.'
}
Set-Location -LiteralPath $stewardProject
& $stewardExecutable health
& $stewardExecutable health --strict
if ($LASTEXITCODE -ne 0) { throw 'Local Steward prerequisites are not ready.' }
```

Plain `health` reports status text; `health --strict` additionally exits 1 when
expected database tables, enabled-root availability, or a nonblank Telegram token
are missing. Zero configured roots is valid for Inbox-only use; disabled roots
do not fail this check. This is local readiness, not an integrity scan or network
probe. Database availability and token configuration do not prove successful
Telegram, model, or OAuth requests. Check `/status` and a
non-sensitive question in Telegram after starting the bot:

```powershell
& $stewardExecutable telegram
```

Stop this foreground instance with Ctrl+C before enabling scheduled operation.
Do not run two pollers for the same token. The runtime lock protects a shared data
directory, not the same token used from another directory or machine.

No activation script is needed when using the virtual environment's absolute
executable. Keep `.env` local, with explicit data/inbox paths where practical.
Shell-only environment variables may not be present in a scheduled process;
existing process environment also takes precedence over `.env`. Do not put keys
in task arguments. Complete OAuth authorization locally before unattended use.

## 2. Register one logon task (explicit operator action)

Run this only after the preflight passes and you want automatic startup. These
commands create a task for the current user's interactive logon. They do not
store a Windows password or arrange execution while that user is logged out.

```powershell
$stewardTaskName = 'Steward Telegram'
$stewardUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (Get-ScheduledTask -TaskName $stewardTaskName -ErrorAction SilentlyContinue) {
    throw 'A task with this name already exists. Inspect it instead of overwriting it.'
}
$stewardAction = New-ScheduledTaskAction -Execute $stewardExecutable -Argument 'telegram' -WorkingDirectory $stewardProject
$stewardTrigger = New-ScheduledTaskTrigger -AtLogOn -User $stewardUser
$stewardPrincipal = New-ScheduledTaskPrincipal -UserId $stewardUser -LogonType Interactive -RunLevel Limited
$stewardTaskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $stewardTaskName -Action $stewardAction -Trigger $stewardTrigger -Principal $stewardPrincipal -Settings $stewardTaskSettings -Description 'Local Steward Telegram polling; no secrets in task arguments.'
```

The working directory is essential for consistent relative paths. This policy
keeps the default battery restrictions and does not wake the computer. If you
need laptop battery operation, choose that explicitly in Task Scheduler after
considering power use. Sleep, logout, offline roots, and network outages can make
Steward unavailable. Failure restarts are bounded and do not fix invalid settings;
some CLI setup errors return normally, so scheduler success is not bot readiness.

## 3. Start and verify

```powershell
Start-ScheduledTask -TaskName $stewardTaskName
Get-ScheduledTask -TaskName $stewardTaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $stewardTaskName | Select-Object LastRunTime, LastTaskResult, NextRunTime
& $stewardExecutable health
```

In Telegram, verify `/status`, a source read, `/pending`, and one non-sensitive
provider request. Confirm an existing pending review survives a stop/start before
relying on unattended operation. A task marked Running proves only that its
process has not exited; it does not prove healthy polling or model responses.
There is no separate watchdog detecting a hung model or polling loop yet.

Use local `steward telegram-deliveries` and `steward telegram-delivery-history`
for delivery diagnostics. Logs may contain personal metadata: inspect locally and
redact before sharing. Never paste `.env` or OAuth token JSON into a bug report.

## 4. Stop, maintain, recover

```powershell
Disable-ScheduledTask -TaskName $stewardTaskName
Stop-ScheduledTask -TaskName $stewardTaskName
Get-ScheduledTask -TaskName $stewardTaskName | Select-Object TaskName, State
```

Stopping through Task Scheduler can terminate an in-flight operation. Prefer a
quiet period with no capture, mutation, or approval underway. Inspect state after
restart rather than blindly resubmitting a write. Confirm all other CLI/watcher
writers are stopped before paired database backup or restore. Follow the README
backup procedure; copy originals separately. Never delete a database or checkpoint
file merely to clear a stuck review. A leftover `telegram-runtime.db` file is not
evidence that a process still holds the lock.

For a missing root, check the drive/mount and OneDrive file availability first.
Keep source files available offline where needed. Never point an authorization
at a broader directory simply to suppress a missing-root error. Do not share live
SQLite databases between machines via file synchronization; keep runtime metadata
local and use explicit snapshots for backup.

If the directory was deliberately moved and the old path no longer exists, keep
all Steward writers stopped and use `steward relocate-root NAME NEW_PATH --confirm`.
The command rebinds only when every tracked relative file has the registered hash;
it changes metadata, not files. Then run `steward scan-root NAME` and `steward
health --strict`. A mismatch requires manual inspection; do not weaken the root
boundary or edit the database to make it pass.

After updates or recovery, run foreground preflight again, stop that foreground
instance, then explicitly re-enable scheduled operation:

```powershell
Enable-ScheduledTask -TaskName $stewardTaskName
Start-ScheduledTask -TaskName $stewardTaskName
```

Deployment acceptance is still required on the actual machine: log out/in, verify
one poller, check available roots, resume a pending review, and exercise outage
recovery using the Telegram manual checklist. This runbook does not certify those
checks as completed.

## 5. Rehearsal record

On 2026-09-13, the repository's disposable local rehearsal passed the following
real code paths without touching the user's operational database or vault:

- a temporary source directory was moved, the old root became unavailable, and
  relocation verified the replacement bytes before preserving the source ID;
- CLI backup and confirmed restore produced write-once snapshots plus the required
  pre-restore safety copy;
- restored knowledge state was reopened and reviewed from a fresh Python process;
- a runtime-lock owner process was terminated and a later process safely acquired
  the same lock;
- an actual refused loopback connection reached the Ollama adapter and returned
  its bounded `ModelGatewayError` without raw socket diagnostics.

The installation's `steward health --strict` preflight passed: both databases were
available, one authorized root was available, and a Telegram token was configured.
On 2026-09-20, the user explicitly authorized registration of the local
`Steward Telegram` task. It uses this project's
virtual-environment `steward.exe`, has `telegram` as its only argument, uses the
project as its working directory, ignores duplicate instances, and has the
documented bounded restart policy. A foreground-poller inspection found no
running Steward Telegram process, after which the task was started and remained
**Running** across a short observation period. Strict local health also passed
while it was running. This proves registration and immediate startup, but not a
future Windows logon trigger, an end-to-end Telegram reply from the scheduled
process, or live Telegram/provider outage recovery. Do not interpret the
disposable recovery rehearsal or task registration alone as full start-at-login
acceptance.

# Windows local operation and start at login

This is an operator procedure, not an automatic installer. Creating a scheduled
task is an explicit deployment action; section 5 records what has been done on
this machine. Keep real Telegram acceptance separate from automated tests.

## 1. Foreground preflight

Use the same ordinary Windows account that owns the authorized folders and the
`.env` file. Do not use SYSTEM or highest privileges. In PowerShell:

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
Telegram or model requests. Send `/home` and a harmless `/find` in Telegram
after starting the bot:

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
in task arguments.

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

In Telegram, verify `/home`, opening and reading a file, `/inbox`, and one
harmless `/find` (a model request). Confirm that replying to an old file card
still targets the same file after a stop/start before relying on unattended
operation. A task marked Running proves only that its
process has not exited; it does not prove healthy polling or model responses.
There is no separate watchdog detecting a hung model or polling loop yet.

Use local `steward telegram-deliveries` and `steward telegram-delivery-history`
for delivery diagnostics. Logs may contain personal metadata: inspect locally and
redact before sharing. Never paste `.env` into a bug report.

## 4. Stop, maintain, recover

```powershell
Disable-ScheduledTask -TaskName $stewardTaskName
Stop-ScheduledTask -TaskName $stewardTaskName
Get-ScheduledTask -TaskName $stewardTaskName | Select-Object TaskName, State
```

Stopping through Task Scheduler can terminate an in-flight operation. Prefer a
quiet period with no capture, mutation, or approval underway. Inspect state after
restart rather than blindly resubmitting a write. Confirm all other CLI
writers are stopped before paired database backup or restore. Follow the README
backup procedure; copy originals separately. Never delete a database merely to clear a
stuck state. A leftover `telegram-runtime.db` file is not
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
one poller, check available roots, confirm the 15-minute rescan sends a filed
notice, and exercise outage recovery using the Telegram manual checklist. This runbook does not certify those
checks as completed.

## 5. Status on this machine

- 13 Sep 2026: a disposable rehearsal passed root relocation (bytes verified
  before keeping source IDs), backup and confirmed restore with a safety copy,
  reopening restored state in a fresh process, runtime-lock takeover after the
  owner process was killed, and a bounded error from an unreachable Ollama.
- 20 Sep 2026: the `Steward Telegram` logon task was registered with the owner's
  approval (the project's `steward.exe`, argument `telegram`, duplicates
  ignored, bounded restarts), started, and stayed **Running**; `health --strict`
  passed.

Not yet proven: the task starting at an actual Windows logon, an end-to-end
Telegram reply from the scheduled process, and recovery from a Telegram or
model-provider outage. Re-run section 1 after updating Steward, and
`steward reextract --all` once after an extractor improvement.
